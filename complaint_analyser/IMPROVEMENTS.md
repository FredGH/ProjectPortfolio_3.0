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

## Local LLM → Paid API Development Strategy

### Is it worth prototyping locally before switching to a paid API?

Yes — this is a well-established pattern: iterate cheaply on a local LLM, then swap to a production-grade API when quality and reliability matter. The core agent logic (prompts, tool definitions, flow control) transfers almost entirely between models.

**Advantages:**
- Free, unlimited local iteration before spending API credits
- No latency or rate-limit friction during early development
- Tool/function calling logic is largely model-agnostic if you abstract the LLM call

**Risks to manage:**
- Small local models (7B–13B) can fail silently on multi-step reasoning, complex tool selection, or structured JSON output — you may debug ghost issues that simply disappear with a better model
- Different models respond differently to system prompts; expect some prompt rework when switching
- Don't wait until the end to test against the target API — do a mid-prototype sanity check to catch divergence early

**Best practice:** Abstract the LLM call behind a single function or class, swappable by config from day one.

---

## Free Alternatives When Local Hardware Is Insufficient

If the local machine can't run a capable model (e.g., Llama 3.1 8B), the recommended free options are:

### Groq (recommended for agent prototyping)
- 300–1,000 tokens/second on custom LPU hardware — speed matters when multi-step tool calls compound
- Free tier: Llama 3.1 8B (`llama-3.1-8b-instant`), Qwen3, DeepSeek-R1, and others; no credit card required
- Llama 3.1 8B free tier: 14,400 requests/day, 500,000 tokens/day
- Fully OpenAI SDK-compatible — swapping to Claude or another paid API later is just a config change

### Google AI Studio
- Free tier: 1,500 requests/day, 1M tokens/minute — more generous on volume than Groq
- Runs Gemma models rather than Llama

### Hugging Face Spaces
- Easy browser-based access to Llama 3 and other models via community Spaces
- Less suited to API-driven agent work

### Also consider: Qwen3
- Strong reasoning and coding performance, Apache 2.0 licence (no commercial restrictions)
- Qwen3 8B or 14B is the practical local choice for most developers in 2026
- Also available on Groq's free tier

### Recommended approach
Use **Groq's free tier** (`llama-3.1-8b-instant` or `qwen3-8b`) for agent prototyping: API-based so code already looks like production, fast iteration on multi-step agent flows, and the OpenAI-compatible interface makes the eventual switch to Claude or another paid API a single config change.

---
