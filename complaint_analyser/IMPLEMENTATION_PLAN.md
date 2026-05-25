# Complaint Analyser — Incremental Implementation Plan

Current state: documentation only (README.md, ARCHITECTURE.md, one data CSV zip).
Target: fully deployed agentic RAG system on Oracle Cloud Free Tier, gated by a GitHub Actions CI/CD pipeline.

Each phase is a self-contained deliverable that can be committed, reviewed, and deployed independently.

---

## Phase 1 — Repository Scaffold & CI/CD Foundation

*Goal: a passing CI pipeline on an empty-but-valid repo. No application code yet.*

### 1.1 — Git & project skeleton

- [ ] Create `.gitignore` (venv, .env, __pycache__, *.pyc, data/*, *.zip, .coverage)
- [ ] Create top-level `pyproject.toml` declaring the `agentic_triage` package (Python 3.11, no deps yet)
- [ ] Create empty `agentic_triage/__init__.py` and `tests/__init__.py`
- [ ] Create `scripts/validate_configs.py` — stub that exits 0 (no configs to validate yet)
- [ ] Create `docker-compose.yml` — stub with a single `hello-world` service so `docker compose config` passes

**Gate:** `ruff check .` passes on empty package.

### 1.2 — Lint CI job

- [ ] Create `.github/workflows/ci.yml` with only the `lint` job:
  - `actions/checkout@v4`
  - `actions/setup-python@v5` with Python 3.11 and pip cache
  - `pip install ruff isort black`
  - `ruff check . && isort --check . && black --check .`
- [ ] Push to GitHub — confirm the `lint` job turns green

**Gate:** Green `lint` job on every push/PR.

### 1.3 — Test CI job

- [ ] Add a single smoke test `tests/test_smoke.py` (`assertTrue(True)`)
- [ ] Add `[project.optional-dependencies] dev = ["coverage"]` to `pyproject.toml`
- [ ] Add `test` job to `ci.yml`:
  - `pip install -e ".[dev]"`
  - `coverage run -m unittest discover -s tests`
  - `coverage report -m --fail-under=80`
  - Upload `.coverage` as an artifact
- [ ] Push — confirm `test` turns green

**Gate:** Green `test` job; coverage artifact uploaded.

### 1.4 — Config validation CI job

- [ ] Add `validate-configs` job to `ci.yml`:
  - Run `python scripts/validate_configs.py`
  - Run `docker compose config --quiet`
- [ ] Expand `validate_configs.py` to glob `domains/*/config.yaml` and parse each against `DomainConfig` (add the dataclass with no extra deps to make this work now)
- [ ] Push — confirm `validate-configs` turns green

**Gate:** Green `validate-configs` job; domain YAML parsing works end-to-end.

### 1.5 — Docker build CI job (ARM64, GHCR)

- [ ] Create minimal `Dockerfile` stubs for `agentic_triage/`, `spacy_service/`, `bertopic_service/` — each just `FROM python:3.11-slim`
- [ ] Add `build` job to `ci.yml` (matrix: api, spacy_service, bertopic_service):
  - `docker/setup-buildx-action@v3`
  - `docker/login-action@v3` (GHCR via `GITHUB_TOKEN`)
  - `docker/build-push-action@v5` — `platforms: linux/arm64`, push only on `main`
  - GHA cache (`type=gha`)
- [ ] Add `ORACLE_HOST`, `ORACLE_SSH_KEY`, `GHCR_TOKEN` secret placeholders in repo Settings (values can be dummy for now)
- [ ] Push — confirm `build` job runs and pushes stub images to GHCR

**Gate:** Three ARM64 images appear in GHCR under the repo packages.

### 1.6 — Evaluate CI job (stub)

- [ ] Create `evaluation/golden_dataset.json` — empty JSON array `[]`
- [ ] Create `evaluation/eval.py` — stub that reads the dataset and exits 0 when empty
- [ ] Add `evaluate` job to `ci.yml` (runs only on `main`, needs `build`):
  - `pip install -e ".[dev]"`
  - `python evaluation/eval.py --dataset evaluation/golden_dataset.json --f1-threshold 0.80 --faithfulness-threshold 0.75`
  - Upload `evaluation/report.json` as artifact
- [ ] Push on `main` — confirm `evaluate` turns green with empty dataset

**Gate:** Full CI pipeline (lint → test → validate-configs → build → evaluate) green on `main`.

---

## Phase 2 — Oracle Cloud Free Tier Provisioning

*Goal: a live Ubuntu 22.04 ARM instance with Docker, reachable via SSH from GitHub Actions and exposed via Cloudflare Tunnel.*

### 2.1 — OCI instance

- [ ] Log in to console.oracle.com → Compute → Instances → Create Instance

**Image (do this first — it controls which shapes are available):**
- [ ] Click "Change image" → select **Canonical Ubuntu 22.04 Minimal aarch64**
- [ ] Critically: make sure the build says **aarch64** (ARM64), NOT amd64/x86. If you pick the x86 image, A1.Flex will appear greyed out and unselectable.

**Shape:**
- [ ] Click "Change shape" → select the **Arm-based processor** tab (not AMD or Intel)
- [ ] Select `VM.Standard.A1.Flex` — it carries the "Always Free-eligible" badge
- [ ] Set sliders to **4 OCPUs** and **24 GB RAM** — this is the Always Free ceiling
- [ ] The shape browser shows "1 (80 max) OCPUs / 6 (512 max) GB" — the max figures are for paid tiers; 4 OCPUs / 24 GB is the free allocation

> **Common mistakes to avoid:**
> - `VM.Standard.E2.1.Micro` (1 OCPU / 1 GB) is Always Free but nowhere near enough — Ollama alone needs ~6 GB
> - `VM.Standard.E4.Flex` and `E3.Flex` are AMD/Intel paid shapes — do not use them
> - If A1.Flex shows "not compatible with selected image", your image is x86 — go back and change it to aarch64 first
> - If A1.Flex shows "not available in the current availability domain", try a different AD (AD-2, AD-3) or a different region (us-ashburn-1, us-phoenix-1)

**Boot volume:**
- [ ] Set to **50 GB**

**Networking:**
- [ ] If this is a fresh account with no existing VCN, select **"Create new virtual cloud network"** — OCI will auto-create a VCN and public subnet
- [ ] If a VCN already exists, select "Select existing" and pick it — then ensure the subnet is public
- [ ] Confirm a **public IP** will be assigned

**SSH key:**
- [ ] Generate Ed25519 key pair locally:
  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/oracle_complaint
  ```
- [ ] Copy the public key to clipboard:
  ```bash
  pbcopy < ~/.ssh/oracle_complaint.pub
  ```
- [ ] Paste into the OCI SSH key field

- [ ] Launch instance; note the public IP

**Gate:** `ssh -i ~/.ssh/oracle_complaint ubuntu@<PUBLIC_IP>` succeeds.

### 2.2 — OCI security list (firewall)

- [ ] In OCI Console → VCN → Security Lists → Default → Ingress rules:
  - Allow TCP 22 from 0.0.0.0/0 (SSH — required for GitHub Actions deploy)
  - Allow TCP 80 and 443 from 0.0.0.0/0 (Cloudflare Tunnel health checks)
- [ ] Do NOT open 8080 (n8n) or 8000 (API) publicly — Cloudflare Tunnel handles that
- [ ] Enable Ubuntu UFW as an extra layer: `sudo ufw allow 22 && sudo ufw enable`

**Gate:** SSH works; port 443 reachable from outside (test with `curl https://<PUBLIC_IP>` — should get a connection refused, not a timeout).

### 2.3 — Docker & Docker Compose on the instance

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker ubuntu
```

- [ ] Run the above; log out and back in
- [ ] Verify: `docker run --rm hello-world` and `docker compose version`

**Gate:** `docker compose version` prints ≥ 2.20.

### 2.4 — Repo clone on the instance

```bash
sudo mkdir -p /opt/complaint_analyser
sudo chown ubuntu:ubuntu /opt/complaint_analyser
git clone https://github.com/<your-org>/<your-repo>.git /opt/complaint_analyser
```

- [ ] Clone the repo into `/opt/complaint_analyser` on the instance
- [ ] Create `/opt/complaint_analyser/.env` with placeholder secrets (no real values yet):
  ```
  POSTGRES_PASSWORD=changeme
  GHCR_TOKEN=
  ```

**Gate:** `git -C /opt/complaint_analyser log --oneline -1` shows the latest commit.

### 2.5 — Cloudflare Tunnel

- [ ] Log in to dash.cloudflare.com → Zero Trust → Networks → Tunnels → Create tunnel
- [ ] Name: `complaint-analyser-prod`
- [ ] Copy the `cloudflared` install command (ARM64 deb) and run it on the instance
- [ ] Configure routes in the Cloudflare UI:
  - `n8n.<your-domain>` → `http://localhost:5678`
  - `api.<your-domain>` → `http://localhost:8000`
- [ ] Verify the tunnel shows as **Healthy** in the Cloudflare dashboard

**Gate:** `curl https://n8n.<your-domain>` returns an HTTP response (even a 502 is fine — the tunnel is live, n8n is not yet running).

### 2.6 — Wire the deploy job in CI

- [ ] Add real values to GitHub repo secrets: `ORACLE_HOST`, `ORACLE_SSH_KEY`, `GHCR_TOKEN`
- [ ] Add `deploy` job to `ci.yml` (needs `build` + `evaluate`, `main` only, `environment: production`):
  ```yaml
  - uses: appleboy/ssh-action@v1
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
- [ ] Push a commit to `main`; confirm the SSH deploy step executes and `docker compose ps` output appears in the CI log

**Gate:** Full pipeline (lint → test → validate-configs → build → evaluate → deploy) green on `main`. First automated deploy to Oracle Cloud confirmed.

---

## Phase 3 — Docker Compose Stack (All Services)

*Goal: all services start and are healthy on the Oracle instance. No application code yet — use official images.*

### 3.1 — Infrastructure services (no custom build)

- [ ] Expand `docker-compose.yml` with: `qdrant`, `postgres`, `redis`, `n8n`, `ollama`
- [ ] Add named volumes: `qdrant_data`, `postgres_data`, `redis_data`, `n8n_data`
- [ ] Add `sql/init.sql` with `triage_results` and `triage_batches` DDL (from ARCHITECTURE.md)
- [ ] Deploy to Oracle; verify each service starts: `docker compose ps`
- [ ] Pull Ollama models:
  ```bash
  docker compose exec ollama ollama pull llama3.1:8b   # recommended for production inference
  docker compose exec ollama ollama pull nomic-embed-text
  ```
  > On memory-constrained laptops (<6 GB free RAM), `llama3.1:8b` will fail to load at inference time. Pull `llama3.2:1b` as a local fallback for the bootstrap step only — **do not use it for production inference**. The `docker-compose.yml` `OLLAMA_NUM_PARALLEL` is set to 6 for local parallelised bootstrap runs; lower it back to 2 on Oracle (4 Arm cores, production bottleneck is CPU not RAM).

**Gate:** `docker compose ps` shows all infra services healthy; `ollama list` shows both models.

### 3.2 — Application service stubs (api, worker-fast, worker-assess, spacy, bertopic)

- [ ] Replace the `FROM python:3.11-slim` stub Dockerfiles with real ones that install the package
- [ ] Add `api`, `worker-fast`, `worker-assess`, `spacy`, `bertopic` services to `docker-compose.yml` with correct environment variables (`REDIS_URL`, `DATABASE_URL`, `OLLAMA_HOST`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_NUM_CTX`)
- [ ] The services will crash at startup (no code yet) — that is expected; confirm the images pull and containers start before crashing cleanly

**Gate:** `docker compose up` completes without errors on the infra services; stub app containers exit with code 1 (not OOM, not networking error).

---

## Phase 4 — Core Package (`agentic_triage/core/`)

*Goal: the three core modules compile and are importable. CI validate-configs uses the real DomainConfig.*

- [ ] `agentic_triage/core/config.py` — `ScoringDimension`, `PriorityLevel`, `CollectionConfig`, `DomainConfig` dataclasses (exact fields from ARCHITECTURE.md)
- [ ] `agentic_triage/core/state.py` — `TriageState` TypedDict
- [ ] `agentic_triage/core/schema.py` — `TriageResult` Pydantic BaseModel
- [ ] `domains/banking_complaints/config.yaml` — exact YAML from README.md
- [ ] `domains/banking_complaints/keywords.txt` — placeholder list (5–10 banking terms)
- [ ] Update `scripts/validate_configs.py` to import `DomainConfig` and parse all YAMLs; fail on schema errors
- [ ] Add unit tests: `tests/core/test_config.py` — round-trip YAML parse; `tests/core/test_schema.py` — Pydantic validation
- [ ] Update `pyproject.toml` dependencies: `pydantic>=2`, `pyyaml`

**Gate:** `python scripts/validate_configs.py` exits 0; coverage ≥ 80% on `core/`; CI green.

---

## Phase 5 — Preprocessing Pipeline

*Goal: sanitizer, NER, spell correction, and keyword matching run locally. spaCy service starts in Docker.*

- [ ] `agentic_triage/preprocessing/sanitizer.py` — regex stripping + XML fence
- [ ] `agentic_triage/preprocessing/ner.py` — spaCy `en_core_web_lg` + GLiNER (label-driven)
- [ ] `agentic_triage/preprocessing/normalizer.py` — SymSpell wrapper with custom vocab path
- [ ] `agentic_triage/preprocessing/keyword.py` — FlashText matcher
- [ ] `spacy_service/main.py` — FastAPI endpoint wrapping spaCy + GLiNER
- [ ] Add unit tests for each preprocessor (mock spaCy model in unit tests; integration tests use real model)
- [ ] Update `pyproject.toml` deps: `spacy`, `gliner`, `symspellpy`, `flashtext`
- [ ] Update `spacy_service/Dockerfile` to install spaCy and download `en_core_web_lg`

**Gate:** All preprocessor unit tests pass; `spacy_service` container starts and responds to a test NER request; CI green.

---

## Phase 6 — Retrieval & Knowledge Base Ingest

*Goal: Qdrant collections populated; recall@5 ≥ 0.70 per collection verified.*

- [ ] `agentic_triage/retrieval/qdrant.py` — `QdrantRetriever` with hybrid search and `search_mode` awareness
- [ ] `agentic_triage/retrieval/reranker.py` — cross-encoder reranker (`cross-encoder/ms-marco-MiniLM-L-6-v2`)
- [ ] `scripts/ingest_kb.py` — full ingest script from ARCHITECTURE.md (regulatory, taxonomy, complaints)
- [ ] Run `bootstrap_labels.py` to produce `data/complaints_labelled.jsonl`; spot-check 5–10 rows for label quality.
  **Recommended model:** `llama3.1:8b` (best quality, requires ~6 GB free RAM). On memory-constrained machines use `llama3.2:1b`.
  **Parallelise the run** using `--offset` / `--limit` to split the corpus across N concurrent processes matching `OLLAMA_NUM_PARALLEL` in `docker-compose.yml` (default: 6 for local dev). See the bootstrap parallelism note in `README.md` for the full command.
  **Alternative:** Anthropic Claude API (`claude-haiku-4-5`) via `--provider anthropic` — no memory constraint, highest quality, small API cost (~$0.02–0.05 for 200 rows).
- [ ] Add source data placeholders to `data/` (FCA/GDPR stubs as plain text; `taxonomy.yaml` with 5 entries); `ingest_kb.py` reads from `data/complaints_labelled.jsonl` not raw CSV
- [ ] `evaluation/kb_test_queries.json` — 5 test queries per collection
- [ ] `evaluation/eval_kb.py` — recall@k per collection; fail below 0.70
- [ ] Run ingest locally against a running Qdrant; verify recall@5 passes
- [ ] Update `pyproject.toml` deps: `qdrant-client`, `sentence-transformers`, `langchain-text-splitters`

**Gate:** `python evaluation/eval_kb.py` passes recall@5 ≥ 0.70 for all three collections; CI green.

---

## Phase 7 — LangGraph Agent

*Goal: full agentic loop runs end-to-end in isolation (no workers yet — direct graph invocation).*

- [ ] `agentic_triage/agent/pre_filter.py` — `is_auto_p4()` + `apply_auto_p4()`
- [ ] `agentic_triage/agent/confidence.py` — structural confidence: score divergence + retrieval similarity
- [ ] `agentic_triage/agent/query_rewriter.py` — `make_query_rewrite_node()` (Ollama at `temperature=0.0`)
- [ ] `agentic_triage/agent/nodes.py` — all node implementations (sanitize, preprocess, hyde, multi_query, retrieve, pre_filter, assess, query_rewrite, finalize)
- [ ] `agentic_triage/agent/tools.py` — tool factory keyed by `col.name`
- [ ] `agentic_triage/agent/graph.py` — `build_graph(config)` factory with conditional edges
- [ ] `agentic_triage/scoring/scorer.py` — weighted composite + escalation override
- [ ] `agentic_triage/reporting/reporter.py` — LLM report generator
- [ ] Add unit tests for `pre_filter`, `confidence`, `scorer`; integration test running the graph on one complaint against a live Qdrant + Ollama
- [ ] Update `pyproject.toml` deps: `langgraph`, `langchain-ollama`

**Gate:** `graph.invoke({...})` on one banking complaint returns a valid `TriageResult` with priority, scores, and reasoning; CI green.

---

## Phase 8 — arq Workers & FastAPI

*Goal: the HTTP API accepts a batch, workers process it, results land in Postgres.*

- [ ] `agentic_triage/workers/tasks.py` — `fast_preprocess_task`, `assess_task`, `FastWorkerSettings`, `AssessWorkerSettings`
- [ ] `agentic_triage/api/router.py` — `/batch/submit/{domain}`, `/batch/{id}/status`, `/report/{domain}` endpoints
- [ ] `agentic_triage/api/factory.py` — `create_multi_domain_app(configs)` auto-discover domains
- [ ] `agentic_triage/db.py` — async Postgres helpers (`increment_batch_counter`, `write_triage_result`)
- [ ] `agentic_triage/retrieval/feedback.py` — `ingest_confirmed_result()` write-back
- [ ] `sql/init.sql` — finalize with all tables: `triage_results`, `triage_batches`, `drift_events`, `recalibration_alerts`
- [ ] Wire `worker-fast` and `worker-assess` Docker Compose services with correct `command` overrides
- [ ] Integration test: POST to `/batch/submit/banking_complaints` with 3 items; poll status; confirm results in Postgres
- [ ] Update `pyproject.toml` deps: `fastapi`, `uvicorn`, `arq`, `asyncpg`

**Gate:** End-to-end batch test passes locally and in CI (using Testcontainers or a `docker compose` service in the CI job); API container starts healthy on Oracle.

---

## Phase 9 — Semantic Cache

*Goal: near-identical complaints hit the cache and skip the full pipeline.*

- [ ] `agentic_triage/retrieval/cache.py` — `lookup_cache()` + `write_cache()` (cosine ≥ 0.97, domain-scoped, 30-day TTL)
- [ ] Wire cache check at the start of `fast_preprocess_task` (before sanitize)
- [ ] Add `query_cache` Qdrant collection creation to `scripts/ingest_kb.py` (empty collection, created at startup)
- [ ] Unit test: two near-identical complaints — second should hit cache; dissimilar complaint should miss
- [ ] Log cache hit rate per batch to `triage_batches` table (add `cache_hits` column)

**Gate:** Cache hit test passes; cache hit rate column visible in Postgres after a batch run.

---

## Phase 10 — Qdrant Migrations

*Goal: schema changes are versioned, idempotent, and applied automatically at API startup.*

- [ ] `migrations/0001_add_outcome_field_to_complaints_history.py` — from ARCHITECTURE.md
- [ ] `migrations/0002_add_hybrid_index_to_regulatory_rules.py`
- [ ] `scripts/run_migrations.py` — apply pending migrations at startup; log to `_migrations` collection
- [ ] Update `api` Dockerfile entrypoint: `python scripts/run_migrations.py && uvicorn ...`
- [ ] Add CI step in `validate-configs` job: `python -c "import migrations.0001_add_outcome_field_to_complaints_history"` (parse check)

**Gate:** Migrations apply cleanly on a fresh Qdrant; re-running is a no-op; CI green.

---

## Phase 11 — n8n Workflows

*Goal: all three workflows imported into n8n and manually verified end-to-end.*

- [ ] `n8n/workflow_triage_batch.json` — Workflow 1 (daily 06:00 cron, batch submit + poll + report)
- [ ] `n8n/workflow_override_ingestion.json` — Workflow 2 (analyst override webhook → Qdrant write-back)
- [ ] `n8n/workflow_drift_response.json` — Workflow 3 (weekly BERTopic diff → ticket + cache TTL expiry)
- [ ] Import all three into the n8n instance on Oracle via the n8n UI
- [ ] Manually trigger Workflow 1 against a small test batch; confirm report email/webhook fires
- [ ] Activate all three workflows

**Gate:** Workflow 1 completes a batch end-to-end; n8n execution log shows no errors.

---

## Phase 12 — BERTopic Service

*Goal: BERTopic drift detection runs on demand (Docker Compose `batch` profile).*

- [ ] `bertopic_service/main.py` — FastAPI endpoint: `GET /clusters/diff` returns new clusters vs. prior week
- [ ] Update `bertopic_service/Dockerfile` — install BERTopic + sentence-transformers (heavy image, ~2 GB)
- [ ] Verify the `batch` profile starts and stops the service cleanly: `docker compose --profile batch up bertopic`
- [ ] Workflow 3 calls the service weekly; test the Slack/Linear alert path manually

**Gate:** `GET /clusters/diff` returns valid JSON; n8n Workflow 3 creates a test ticket.

---

## Phase 13 — Evaluation Harness & RAGAS Gate

*Goal: golden dataset populated; CI `evaluate` job enforces quality thresholds.*

- [ ] Expand `evaluation/golden_dataset.json` to ≥ 20 labelled complaints (extract from `complaints.csv` zip)
- [ ] Implement `evaluation/eval.py` — RAGAS runner: faithfulness ≥ 0.75, F1 ≥ 0.80
- [ ] Run `eval.py` locally; tune prompts or config if thresholds are not met
- [ ] CI `evaluate` job now uses real dataset and real thresholds — merge to `main` is blocked if RAGAS fails

**Gate:** RAGAS thresholds pass on `main`; evaluation report artifact pinned to each commit SHA.

---

## Phase 14 — Production Hardening

*Goal: the system is stable, monitored, and ready for real complaint traffic.*

- [ ] Set `POSTGRES_PASSWORD` from a proper Docker secret (not a plain env var)
- [ ] Configure Postgres daily backup to OCI Object Storage (free 20 GB)
- [ ] Set up `docker compose` restart policies (`restart: unless-stopped`) on all services
- [ ] Add healthcheck endpoints to `api` and `spacy` services; reference in `docker-compose.yml`
- [ ] Document `OLLAMA_NUM_PARALLEL=2` and `OLLAMA_NUM_CTX=2048` tuning in README.md Production section
- [ ] Add `domains/security_alerts/config.yaml` as a second domain (proves the multi-domain path)
- [ ] Verify full RAM footprint is within 24 GB: `docker stats --no-stream`

**Gate:** `docker stats` steady-state ≤ 13 GB RAM; all services pass healthchecks; second domain processes a test batch.

---

## Dependency Map

```
Phase 1 (CI/CD)
    └── Phase 2 (Oracle provisioning)
            └── Phase 3 (Docker Compose stack)
                    └── Phase 4 (Core package)
                            ├── Phase 5 (Preprocessing)
                            │       └── Phase 6 (Retrieval + KB ingest)
                            │               └── Phase 7 (LangGraph agent)
                            │                       └── Phase 8 (Workers + FastAPI)
                            │                               ├── Phase 9 (Semantic cache)
                            │                               ├── Phase 10 (Migrations)
                            │                               └── Phase 11 (n8n workflows)
                            └── Phase 12 (BERTopic) ← can run in parallel with 8–11
Phase 13 (RAGAS gate) ← needs Phase 7 + golden dataset
Phase 14 (Hardening) ← final, needs all prior phases
```

---

## Quick-reference: GitHub Secrets Required

| Secret | When needed | Value |
|---|---|---|
| `ORACLE_HOST` | Phase 2.6 | Oracle instance public IP |
| `ORACLE_SSH_KEY` | Phase 2.6 | Ed25519 private key for `ubuntu` user |
| `GHCR_TOKEN` | Phase 2.6 | GitHub PAT with `read:packages` scope |
| `GITHUB_TOKEN` | Phase 1.5 | Auto-provided by GitHub Actions |

---

## Quick-reference: Oracle Instance Setup Commands

```bash
# After SSH in (Phase 2.1):
ssh -i ~/.ssh/oracle_complaint ubuntu@<PUBLIC_IP>

# Docker install (Phase 2.3)
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker ubuntu

# Repo clone (Phase 2.4)
sudo mkdir -p /opt/complaint_analyser && sudo chown ubuntu:ubuntu /opt/complaint_analyser
git clone https://github.com/<org>/<repo>.git /opt/complaint_analyser

# Pull Ollama models (Phase 3.1)
# Production (Oracle, 24 GB RAM): use llama3.1:8b
docker compose exec ollama ollama pull llama3.1:8b
docker compose exec ollama ollama pull nomic-embed-text
# Local bootstrap fallback only (memory-constrained laptop, <6 GB free):
# docker compose exec ollama ollama pull llama3.2:1b
```
