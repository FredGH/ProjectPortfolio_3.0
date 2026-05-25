# Improvements & Optimisation Notes

Decisions, trade-offs, and future opportunities identified during development.

---

## Parallelisation Opportunities

### Already implemented

| Where | What | Detail |
|---|---|---|
| `scripts/bootstrap_labels.py` | Parallel corpus labelling | `--offset` / `--limit` flags split the CSV into N slices; each slice is a separate process calling Ollama concurrently. `OLLAMA_NUM_PARALLEL=6` in `docker-compose.yml` matches the number of processes. |

### Retrieval fan-out inside the LangGraph agent (highest impact)

Each complaint currently queries the three Qdrant collections (`regulatory_rules`, `risk_taxonomy`, `complaints_history`) sequentially. These searches are fully independent and can be fired simultaneously with `asyncio.gather()` in `agent/nodes.py`:

```python
results = await asyncio.gather(
    retriever.search("regulatory_rules", query),
    retriever.search("risk_taxonomy", query),
    retriever.search("complaints_history", query),
)
```

This collapses 3× serial Qdrant round-trips into a single latency hit — the single biggest per-complaint speed improvement with minimal code change.

### Multi-query / HyDE expansion

The `query_rewriter.py` node generates N query variants for re-retrieval. Each variant searches all 3 collections, giving N×3 serial calls. These can be parallelised with the same `asyncio.gather()` pattern — N×3 searches in one round rather than N×3 sequential calls.

### arq worker concurrency tuning

`worker-fast` runs 3 concurrent jobs; `worker-assess` runs 2. These are the correct handles for batch throughput. On Oracle (4 Arm cores, 24 GB RAM) increasing `OLLAMA_NUM_PARALLEL` from 2 and raising arq concurrency proportionally scales inference throughput linearly up to the CPU/memory ceiling.

### KB ingest — 3 collections in parallel

`scripts/ingest_kb.py` currently ingests one collection at a time. The three ingest operations are independent and can be launched as parallel subprocesses (same approach as bootstrap), saving ~3× on the one-time setup cost.

### Embedding batches at ingest and query time

`nomic-embed-text` via `/api/embed` already accepts a list of texts (the script batches 64 texts per call). If multiple batches are queued, those HTTP calls can be fired in parallel against Ollama's embedding slots, which are separate from the LLM inference slots.

### Summary by impact

| Opportunity | Latency impact | Complexity |
|---|---|---|
| Retrieval fan-out (3 collections in parallel) | ~3× per complaint | Low — `asyncio.gather` in `agent/nodes.py` |
| Multi-query parallel search | ~N× on re-retrieval path | Low — same pattern |
| arq worker concurrency tuning | Batch throughput scaling | Low — env var |
| Parallel collection ingest | ~3× on one-time setup | Low — subprocess |
| Parallel embedding batches | Marginal | Medium |

---

## Bootstrap Model Choice

`scripts/bootstrap_labels.py` generates seed labels for `complaints_history`. Model options ranked by quality:

1. **`llama3.1:8b`** (recommended) — best label quality; requires ~6 GB free RAM
2. **Anthropic `claude-haiku-4-5`** — comparable or better quality, no memory constraint, ~$0.02–0.05 for 200 rows; requires `ANTHROPIC_API_KEY` in `.env`
3. **`llama3.2:1b`** (fallback) — fits in <2 GB; noticeably weaker instruction following but sufficient for seeding the collection

On memory-constrained machines where `llama3.1:8b` cannot load, parallelise with `llama3.2:1b` to compensate for lower per-call quality with higher throughput. See `README.md` bootstrap parallelism note for the full command.

Note: `llama3.1:8b` remains the production inference model for the LangGraph agent — the model choice here applies only to the one-time bootstrap step.

---
