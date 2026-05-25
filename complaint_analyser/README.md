# Complaint Analyser

A production-ready agentic RAG system for triage and urgency scoring of unstructured complaint data. Built on a fully free, self-hosted stack targeting Oracle Cloud Free Tier.

For system design, component descriptions, and architectural decisions see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Business Context

A commercial bank's customer support desk receives a continuous stream of unstructured complaint data with no systematic way to distinguish critical issues from routine ones. High-stakes complaints — those involving potential regulatory exposure, data protection breaches, or direct financial harm — are handled in the same queue as low-priority enquiries, creating operational risk and delaying response to the most vulnerable customers.

Urgency is determined by two dimensions:

- **Reputational risk to the bank** — regulatory breaches, GDPR violations, incorrect fee calculations
- **Direct financial impact to the client** — unauthorised transactions, frozen accounts, failed payments

---

## How Labels Are Generated

There are three distinct types of labels in this system, each with a different origin:

| Label type | Who creates it | When |
|---|---|---|
| **NER entity labels** (e.g. `MONEY`, `DATE`, `ORG`) | Human, at config time | Defined once in `domains/*/config.yaml` under `ner_labels`; spaCy and GLiNER use them at inference time. No runtime labelling needed. |
| **Priority labels** (P1–P4) | LLM, at inference time | Ollama (`llama3.1:8b`) assigns priority and dimension scores for every new complaint. Thresholds are human-calibrated via `priority_levels` in the domain config. Analyst overrides (via n8n Workflow 2) are written back to `complaints_history`, gradually shifting the precedent store toward higher-quality examples. |
| **Seed / historical labels** | LLM-bootstrapped, then human-curated | The raw source data (`Consmer_Complaints_Processed.csv`) contains only `product` and `narrative` — no pre-existing priority or score columns. Before the first ingest, `scripts/bootstrap_labels.py` calls Ollama on every narrative to generate `priority`, `dimension_scores`, `composite_score`, `confidence`, `reasoning`, and `recommended_action`, and writes the result to `data/complaints_labelled.jsonl`. A human then spot-checks quality and manually curates 20+ high-confidence rows into `evaluation/golden_dataset.json` for use by the RAGAS evaluation harness. **Model choice:** `llama3.1:8b` is recommended for best label quality. On machines with <6 GB of free RAM, use `llama3.2:1b` (see bootstrap parallelism note below) or the Anthropic Claude API (set `ANTHROPIC_API_KEY` in `.env` and pass `--provider anthropic` — zero memory overhead, highest quality). |

The system is **not pre-trained** on human-labelled data. It bootstraps itself from LLM-generated labels and self-reinforces over time as analysts confirm or override results.

---

## Self-Reinforcement Mechanism

The system improves over time through two feedback paths, both routing analyst signal back into the `complaints_history` Qdrant collection, which acts as the growing precedent store.

### Path 1 — Analyst Overrides (active, high-signal)

When an analyst corrects a triage label in the support UI, **n8n Workflow 2** fires automatically:

1. Updates `triage_results.analyst_override` in Postgres with the corrected priority.
2. POSTs the corrected `TriageResult` to the `/feedback` FastAPI endpoint.
3. Re-embeds `cleaned_text` and **upserts the record into `complaints_history`** with the analyst-assigned priority as the authoritative label.

Future similar complaints retrieve this corrected example as a calibrated precedent during the RAG step, gradually shifting the precedent store toward higher-quality, human-validated examples.

### Path 2 — Silent Confirmation (passive, low-signal)

After 30 days, any P1/P2 complaint that was **not** overridden is automatically ingested into `complaints_history` by a weekly cron via Workflow 2. This grows the precedent base without requiring analyst action on every item.

### Recalibration Trigger

A rolling 7-day override rate is tracked per priority level. If overrides for any level exceed **15%**, a recalibration alert is written to `recalibration_alerts` in Postgres and surfaced to the team. This signals that LLM thresholds or precedent examples need human review before the drift compounds.

---

## Tech Stack

| Layer                | Technology                            | Why                                                                    |
| -------------------- | ------------------------------------- | ---------------------------------------------------------------------- |
| **Workflow**         | n8n (self-hosted)                     | Triggers, routing, report delivery                                     |
| **LLM**              | Ollama + `llama3.1:8b` (Q4 quantized) | Runs locally, 0 API cost                                               |
| **Embeddings**       | Ollama `nomic-embed-text`             | Co-located with LLM, no separate service                               |
| **Vector store**     | Qdrant (Docker)                       | Hybrid search, filtering by metadata fields                            |
| **NER**              | spaCy `en_core_web_lg` + GLiNER       | spaCy for standard entities, GLiNER for zero-shot domain-specific ones |
| **Keyword matching** | FlashText                             | 10x faster than regex on large keyword libraries                       |
| **Spell correction** | SymSpell + custom domain vocab        | Domain-aware correction without false positives                        |
| **Topic modeling**   | BERTopic                              | Unsupervised cluster detection for emerging themes                     |
| **Agent framework**  | LangGraph                             | Stateful loop with conditional re-retrieval                            |
| **Job queue**        | arq + Redis 7                         | Async task queue — parallelises preprocessing; serialises Ollama calls |
| **API layer**        | FastAPI                               | n8n calls the agentic service via HTTP                                 |
| **Containerisation** | Docker Compose                        | Ties all services together                                             |

---

## Project Structure

```
complaint_analyser/
├── agentic_triage/              ← reusable framework package
│   ├── core/
│   │   ├── config.py            ← DomainConfig, ScoringDimension, PriorityLevel, CollectionConfig
│   │   ├── state.py             ← Generic TriageState (TypedDict for LangGraph)
│   │   └── schema.py            ← Generic TriageResult output model
│   ├── preprocessing/
│   │   ├── base.py              ← AbstractPreprocessor protocol
│   │   ├── sanitizer.py         ← prompt-injection stripping; XML-fence wrapper for LLM input
│   │   ├── ner.py               ← spaCy + GLiNER (label-driven via config)
│   │   ├── normalizer.py        ← SymSpell wrapper (custom vocab via config)
│   │   └── keyword.py           ← FlashText matcher (library path via config)
│   ├── retrieval/
│   │   ├── base.py              ← AbstractRetriever protocol
│   │   ├── qdrant.py            ← QdrantRetriever (collection-agnostic, search_mode-aware)
│   │   ├── feedback.py          ← ingest_confirmed_result() — writes back to precedent collection
│   │   ├── cache.py             ← semantic cache: lookup_cache() + write_cache() via query_cache Qdrant collection
│   │   ├── hyde.py              ← HyDE: generate hypothetical doc before retrieval
│   │   ├── multi_query.py       ← multi-query: decompose into N sub-queries, merge results
│   │   └── reranker.py          ← cross-encoder reranker: position-aware context ordering
│   ├── agent/
│   │   ├── graph.py             ← build_graph(config) factory — sanitize/preprocess/hyde/multi_query/retrieve/pre_filter/assess/query_rewrite/finalize
│   │   ├── nodes.py             ← node implementations for each graph step
│   │   ├── pre_filter.py        ← is_auto_p4() + apply_auto_p4() — deterministic P4 classifier
│   │   ├── confidence.py        ← structural confidence: score_divergence + retrieval_similarity
│   │   ├── query_rewriter.py    ← make_query_rewrite_node() — Ollama reformulates cleaned_text for low-scoring collections
│   │   └── tools.py             ← tool factory: one retrieve tool per CollectionConfig (keyed by name)
│   ├── scoring/
│   │   └── scorer.py            ← weighted composite + escalate_if_any_dimension_exceeds logic
│   ├── reporting/
│   │   └── reporter.py          ← LLM report generator (template-driven)
│   ├── workers/
│   │   └── tasks.py             ← arq task definitions: fast_preprocess_task + assess_task;
│   │                               FastWorkerSettings (max_jobs=3) + AssessWorkerSettings (max_jobs=2)
│   └── api/
│       ├── factory.py           ← create_multi_domain_app(configs) → FastAPI instance
│       └── router.py            ← /batch/submit/{domain}, /batch/{id}/status, /report/{domain}
├── domains/                     ← domain configs (not part of the package)
│   ├── banking_complaints/
│   │   ├── config.yaml
│   │   └── keywords.txt
│   └── security_alerts/
│       ├── config.yaml
│       └── keywords.txt
├── migrations/                  ← Qdrant collection schema migrations (idempotent, versioned)
│   ├── 0001_add_outcome_field_to_complaints_history.py
│   └── 0002_add_hybrid_index_to_regulatory_rules.py
├── scripts/
│   ├── validate_configs.py      ← CI: parse all domain YAMLs against DomainConfig
│   ├── run_migrations.py        ← entrypoint: apply pending Qdrant migrations at startup
│   ├── ingest_kb.py             ← populate Qdrant collections from source files (regulatory, taxonomy, complaints)
│   ├── bootstrap_labels.py      ← LLM-label raw complaint narratives via Ollama → data/complaints_labelled.jsonl
│   └── peek_data.py             ← dev utility: print first N lines of every file in data/
├── spacy_service/
│   └── main.py                  ← spaCy + GLiNER NER endpoint
├── bertopic_service/
│   └── main.py                  ← BERTopic batch clustering endpoint
├── n8n/
│   ├── workflow_triage_batch.json       ← Workflow 1: daily triage batch
│   ├── workflow_override_ingestion.json ← Workflow 2: analyst override → KB feedback
│   └── workflow_drift_response.json     ← Workflow 3: BERTopic drift → ticket creation
├── data/                        ← source files for KB ingest (gitignored if sensitive)
│   ├── regulatory/              ← FCA / GDPR / PSD2 / internal policy plain-text files
│   ├── taxonomy.yaml            ← list of {label, definition} entries for risk_taxonomy
│   ├── Consmer_Complaints_Processed.csv.zip  ← raw CFPB complaints (product + narrative only; no labels)
│   └── complaints_labelled.jsonl             ← LLM-bootstrapped labels; generated by bootstrap_labels.py
├── evaluation/
│   ├── golden_dataset.json      ← 20+ manually curated items (hand-picked from bootstrap output + analyst reviews)
│   ├── eval.py                  ← RAGAS evaluation runner (faithfulness + context precision/recall)
│   ├── eval_kb.py               ← recall@k evaluation per Qdrant collection
│   └── kb_test_queries.json     ← test set: [{query, collection, expected_ids}] entries
├── docker-compose.yml
├── README.md
└── ARCHITECTURE.md
```

---

## Domain Configuration

A new domain requires only a YAML config file and Qdrant collections. No framework code changes needed.

### Banking Complaints (`domains/banking_complaints/config.yaml`)

```yaml
domain_name: banking_complaints
input_field: complaint_text
id_prefix: "C-"
confidence_threshold: 0.7
max_reretrieval_loops: 2
ner_labels: [MONEY, DATE, ORG, PRODUCT, PERSON]
keyword_library_path: ./keywords.txt

scoring_dimensions:
  - name: reputational_risk
    description: Regulatory breaches, GDPR violations, fee errors
    weight: 1.0
    high_score_examples:
      - GDPR breach with third-party data exposure
      - Incorrect fee charged in breach of FCA rules
  - name: financial_impact
    description: Direct financial harm to the customer
    weight: 1.0
    high_score_examples:
      - Unauthorised transaction on frozen account
      - Failed payment causing downstream penalties

priority_levels:
  - label: P1
    min_composite: 8.0
    escalate_if_any_dimension_exceeds: 4.0
    description: Regulatory breach or immediate financial loss
    response_sla: "4 hours"
    recommended_action: Escalate to DPO or compliance team immediately
  - label: P2
    min_composite: 5.0
    description: Reputational risk or significant financial impact
    response_sla: Same day
    recommended_action: Assign to senior support agent
  - label: P3
    min_composite: 3.0
    description: Process failure, moderate impact
    response_sla: 24–48 hours
    recommended_action: Standard escalation queue
  - label: P4
    min_composite: 0.0
    description: General enquiry or minor issue
    response_sla: Standard queue
    recommended_action: First-line support

collections:
  - name: complaints_history
    role: precedent
    top_k: 5
    filter_fields: [category, date]
    search_mode: hybrid
  - name: regulatory_rules
    role: rules
    top_k: 3
    search_mode: hybrid
  - name: risk_taxonomy
    role: rubric
    top_k: 2
    search_mode: dense
```

### Security Alerts (`domains/security_alerts/config.yaml`)

```yaml
domain_name: security_alerts
input_field: alert_description
id_prefix: "SEC-"
confidence_threshold: 0.8
max_reretrieval_loops: 3
ner_labels: [IP_ADDRESS, CVE_ID, SYSTEM, USER]
keyword_library_path: ./keywords.txt

scoring_dimensions:
  - name: blast_radius
    description: How many systems or users could be affected
    weight: 1.5
  - name: exploitability
    description: How easily can an attacker exploit this
    weight: 1.0

priority_levels:
  - label: SEV1
    min_composite: 10.0
    escalate_if_any_dimension_exceeds: 4.0
    response_sla: "15 minutes"
    recommended_action: Page on-call security engineer and lock affected systems
  - label: SEV2
    min_composite: 6.0
    response_sla: "1 hour"
    recommended_action: Open incident ticket and notify security team

collections:
  - name: past_incidents
    role: precedent
    top_k: 5
    search_mode: hybrid
  - name: cve_database
    role: rules
    top_k: 3
    search_mode: hybrid
  - name: severity_rubric
    role: rubric
    top_k: 2
    search_mode: dense
```

### Adding a New Domain

All domains are served by a single FastAPI instance. Routes are namespaced by domain name so multiple domains can be added without restarting or redeploying — only a new YAML file and Qdrant collection ingest are needed.

```python
# main.py — domains auto-discovered at startup
configs = {
    path.parent.name: DomainConfig(**yaml.safe_load(path.read_text()))
    for path in Path("domains").rglob("config.yaml")
}
app = create_multi_domain_app(configs)
```

---

## Build Order

1. Docker Compose skeleton — Ollama + Qdrant + n8n + FastAPI + Postgres + Redis
2. Implement `agentic_triage` core package — `DomainConfig`, `TriageState`, `TriageResult`
3. Implement preprocessing nodes — sanitizer, NER (label-driven), SymSpell, FlashText
4. Implement `QdrantRetriever` (hybrid search, `search_mode`-aware) and tool factory (keyed by `col.name`)
5. Implement KB ingest pipeline (`scripts/ingest_kb.py`) — populate `complaints_history`, `regulatory_rules`, `risk_taxonomy` from source files; run `eval_kb.py` to verify recall@5 ≥ 0.70 per collection before proceeding. Bootstrap seed labels first with `bootstrap_labels.py` (see bootstrap parallelism note below).
6. Implement structural confidence (`confidence.py`) — score divergence + retrieval similarity
7. Implement `pre_filter.py` — `is_auto_p4()` + `apply_auto_p4()` deterministic classifier
8. Implement generic LangGraph agent with `build_graph(config)` — sanitize → preprocess → retrieve → pre_filter → assess → query_rewrite → finalize; `escalate_if_any_dimension_exceeds` in scorer; `query_rewriter.py` node wired on re-retrieval path
9. Implement arq workers (`workers/tasks.py`) — `FastWorkerSettings` (3 jobs) + `AssessWorkerSettings` (2 jobs); wire to Redis queue
10. Implement semantic cache (`retrieval/cache.py`) — `query_cache` Qdrant collection; wire `lookup_cache` / `write_cache` into `fast_preprocess_task`; add TTL expiry to n8n Workflow 3
11. Wire up `create_multi_domain_app(configs)` FastAPI factory — `/batch/submit/{domain}` + `/batch/{id}/status` + `/report/{domain}` endpoints
12. Create `triage_results` + `triage_batches` Postgres tables; idempotency on batch submit
13. Write `domains/banking_complaints/config.yaml`, run migrations
14. n8n Workflow 1 — batch submit + poll loop + report; Workflow 2 — override ingestion; Workflow 3 — drift response + cache TTL expiry
15. Deploy to Oracle Cloud (`OLLAMA_NUM_PARALLEL=2`, `OLLAMA_NUM_CTX=2048`), expose via Cloudflare Tunnel
16. Evaluation harness — labelled test set (`golden_dataset.json`), KB recall (`kb_test_queries.json`), override-rate, pre-filter deflection rate, cache hit rate monitoring

---

## Production Deployment

### Oracle Cloud Free Tier

**Free allocation:** 4× Arm A1 cores · 24 GB RAM · 200 GB block storage · Ubuntu 22.04

**Service RAM footprint:**

| Service                             | RAM               |
| ----------------------------------- | ----------------- |
| Ollama (`llama3.1:8b` Q4)           | ~6 GB             |
| Qdrant                              | ~1 GB             |
| Postgres                            | ~256 MB           |
| Redis 7                             | ~128 MB           |
| n8n                                 | ~512 MB           |
| FastAPI + LangGraph                 | ~1 GB             |
| worker-fast (3 concurrent jobs)     | ~768 MB           |
| worker-assess (2 concurrent jobs)   | ~512 MB           |
| spaCy NER service                   | ~512 MB           |
| BERTopic (scheduled, not always-on) | ~2 GB             |
| **Total steady-state**              | **~10.7–12.7 GB** |

Comfortable within the 24 GB allocation. **Public access:** Cloudflare Tunnel (free) exposes n8n and the API without opening firewall ports or requiring a custom domain.

> **Bootstrap parallelism note (local development):** `scripts/bootstrap_labels.py` generates seed labels for the `complaints_history` collection. On Oracle (24 GB, 4 cores), run with the default `llama3.1:8b` model — it is the recommended model for label quality. On a memory-constrained laptop, use `llama3.2:1b` instead. Either way, parallelise the run by splitting the corpus into N slices using `--offset` and `--limit`, writing each slice to a separate output file, then concatenating before ingest:
>
> ```bash
> # Example: 6 parallel batches of 18 rows each (rows 0–107)
> for i in 0 1 2 3 4 5; do
>   python scripts/bootstrap_labels.py \
>     --offset $((i * 18)) --limit 18 \
>     --model llama3.2:1b \
>     --output data/complaints_labelled_p${i}.jsonl &
> done
> wait
> cat data/complaints_labelled_p*.jsonl >> data/complaints_labelled.jsonl
> ```
>
> **Why 6 parallel and not more?** `OLLAMA_NUM_PARALLEL` is set to 6 in `docker-compose.yml`. This was chosen by balancing three constraints: (1) **RAM** — `llama3.2:1b` weights (~1.3 GB) are loaded once and shared; each additional parallel slot adds ~150 MB of KV-cache overhead at 2048 context, giving ~17 theoretical slots before exhausting 3.9 GB; (2) **CPU threads** — Apple Silicon and similar CPUs see diminishing returns beyond 6–8 concurrent LLM inference threads due to memory-bandwidth saturation; (3) **safety margin** — 6 was chosen to leave headroom for the OS and Docker itself. On the Oracle deployment (24 GB, 4 Arm cores) `OLLAMA_NUM_PARALLEL` should be lowered back to 2 and `llama3.1:8b` used instead, since the production bottleneck is core count, not RAM.
>
> **Alternative (highest quality, no memory constraint):** Call the Anthropic Claude API (`claude-haiku-4-5`) instead of local Ollama. It produces labels comparable to or better than `llama3.1:8b`, completes 200 rows in under 5 minutes, and requires only `ANTHROPIC_API_KEY` in your `.env`. This has a small API cost (~$0.02–0.05 for 200 rows at haiku pricing).

### Docker Compose

```yaml
services:
  ollama:
    image: ollama/ollama
    # LLM + embeddings — pull llama3.1:8b and nomic-embed-text

  qdrant:
    image: qdrant/qdrant
    volumes:
      - qdrant_data:/qdrant/storage

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: triage
      POSTGRES_USER: triage
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./sql/init.sql:/docker-entrypoint-initdb.d/init.sql

  n8n:
    image: n8nio/n8n
    volumes:
      - n8n_data:/home/node/.n8n

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

  api:
    build: ./agentic_triage
    environment:
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql://triage:${POSTGRES_PASSWORD}@postgres:5432/triage

  worker-fast:
    build: ./agentic_triage
    command: python -m arq agentic_triage.workers.tasks.FastWorkerSettings
    environment:
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql://triage:${POSTGRES_PASSWORD}@postgres:5432/triage
      OLLAMA_HOST: http://ollama:11434

  worker-assess:
    build: ./agentic_triage
    command: python -m arq agentic_triage.workers.tasks.AssessWorkerSettings
    environment:
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql://triage:${POSTGRES_PASSWORD}@postgres:5432/triage
      OLLAMA_HOST: http://ollama:11434
      OLLAMA_NUM_PARALLEL: "2"
      OLLAMA_NUM_CTX: "2048"

  spacy:
    build: ./spacy_service

  bertopic:
    build: ./bertopic_service
    profiles: ["batch"]

volumes:
  qdrant_data:
  postgres_data:
  redis_data:
  n8n_data:
```

---

## CI/CD Pipeline

All domain config files, n8n workflow exports, Docker service definitions, and framework code are Git-tracked and validated on every push. Every production deployment is gated on lint, tests, config schema validation, and a RAGAS quality evaluation.

### Pipeline

```
push / PR  →  [lint]  →  [test]  →  [validate-configs]
                                           │
                                     (all pass)
                                           │
                                       [build]   ← matrix: api · spacy_service · bertopic_service
                                           │            push image to GHCR (main only)
                                    (main only)
                                           │
                                      [evaluate]  ← RAGAS on golden_dataset.json
                                           │
                                       [deploy]   ← SSH → Oracle Cloud → docker compose up
```

### `.github/workflows/ci.yml`

```yaml
name: CI/CD

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

env:
  REGISTRY: ghcr.io
  IMAGE_PREFIX: ghcr.io/${{ github.repository }}

jobs:

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install ruff isort black
      - run: ruff check . && isort --check . && black --check .

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: coverage run -m unittest discover -s tests
      - run: coverage report -m --fail-under=80
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: coverage-report
          path: .coverage

  validate-configs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -e .
      - name: Validate domain YAML configs
        run: python scripts/validate_configs.py
      - name: Validate docker-compose syntax
        run: docker compose config --quiet

  build:
    needs: [lint, test, validate-configs]
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: [api, spacy_service, bertopic_service]
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: ./${{ matrix.service }}
          platforms: linux/arm64
          push: ${{ github.ref == 'refs/heads/main' }}
          tags: |
            ${{ env.IMAGE_PREFIX }}/${{ matrix.service }}:${{ github.sha }}
            ${{ env.IMAGE_PREFIX }}/${{ matrix.service }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  evaluate:
    needs: build
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -e ".[dev]"
      - name: Run RAGAS evaluation on golden dataset
        run: |
          python evaluation/eval.py \
            --dataset evaluation/golden_dataset.json \
            --f1-threshold 0.80 \
            --faithfulness-threshold 0.75
      - uses: actions/upload-artifact@v4
        with:
          name: eval-report-${{ github.sha }}
          path: evaluation/report.json

  deploy:
    needs: [build, evaluate]
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: production
    steps:
      - name: Deploy to Oracle Cloud via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.ORACLE_HOST }}
          username: ubuntu
          key: ${{ secrets.ORACLE_SSH_KEY }}
          script: |
            cd /opt/complaint_analyser
            git pull origin main
            echo "${{ secrets.GHCR_TOKEN }}" | \
              docker login ghcr.io -u ${{ github.actor }} --password-stdin
            docker compose pull api spacy
            docker compose up -d --no-deps --wait api spacy
            docker compose ps
```

### Required Secrets

| Secret           | Where to set              | Value                                                                      |
| ---------------- | ------------------------- | -------------------------------------------------------------------------- |
| `ORACLE_HOST`    | Repo → Settings → Secrets | Oracle Cloud instance public IP                                            |
| `ORACLE_SSH_KEY` | Repo → Settings → Secrets | SSH private key (Ed25519) for `ubuntu` user                                |
| `GHCR_TOKEN`     | Repo → Settings → Secrets | GitHub PAT with `read:packages` scope (used by deploy host to pull images) |
| `GITHUB_TOKEN`   | Auto-provided by Actions  | Push images to GHCR during `build`                                         |

### Audit Trail

| Artifact                                          | Tracked via                                                                |
| ------------------------------------------------- | -------------------------------------------------------------------------- |
| Domain configs (`domains/*/config.yaml`)          | Git history — `git log -p domains/` shows every threshold or weight change |
| n8n workflow export (`n8n/complaint_triage.json`) | Git diff shows added/removed/rewired nodes                                 |
| Docker Compose definition (`docker-compose.yml`)  | Git history — service and volume changes are versioned                     |
| RAGAS evaluation results                          | GitHub Actions artifact pinned to commit SHA                               |
| Coverage report                                   | GitHub Actions artifact pinned to commit SHA                               |
