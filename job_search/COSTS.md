# Job Search Platform — Cost Model

Companion to `PLAN.md` and `backlog.yml`.

**Prices verified 23 August 2026.** All figures USD unless marked. GBP
conversions at ~0.78. Cloud pricing assumes `europe-west2` (London).

**Verify before committing:** cloud free-tier limits and LLM rates move.
Every number below is a model, not a quote — the assumptions table is the
part that matters, because it's what you change when reality differs.

---

## TL;DR

**Multi-user note — two users.** Roughly $3 of the ~$10 monthly LLM figure is
shared work that amortises (ingestion, skill extraction, categorisation,
marts, question bank generation); ~$6–7 scales per head (scoring, tailoring,
letters, pitches, alerts). **Two users lands near $17/month**, not $20.

Infrastructure is unchanged — two users is well inside every free tier that
one user was. The Adzuna 1,000-call allowance is the only shared resource
worth a fair-use split, and per-user LLM quotas are worth the few lines to
stop one heavy application month starving the other user's ingestion.

| Scenario | Monthly | Notes |
|---|---|---|
| **Development (dual-track)** | **~$1–2** | Local models + weekly target-provider eval runs. This is most of the build |
| **Local only** | **~$0** | Electricity aside |
| **Local + LLM API** | **~$10–15** | LLM is the entire variable cost |
| **GCP minimal** (Cloud Run + Neon free) | **~$11–17** | Infra ≈ $1–2, rest is LLM |
| **GCP full** (Cloud SQL + n8n VM + IAP) | **~$45–70** | Infra ≈ $35–55 |

**One-off during build: ~$18–25** (Q&A bank generation, golden set, backfills).

The headline: **infrastructure is nearly free; LLM tokens are the only line
that moves.** And even that lands around a tenner a month with disciplined
model routing. The thing most likely to cost you real money is Cloud SQL
sitting idle at £20–30/month for a database you could run free on Neon.

---

## 1. Local

| Item | Cost | Note |
|---|---|---|
| Docker Desktop | $0 | Free for personal use / small business |
| Postgres + pgvector | $0 | Container |
| Ollama + embedding model | $0 | Local inference |
| n8n | $0 | Self-hosted container |
| dlt, dbt-core, Streamlit, FastAPI | $0 | All open source |
| Jira Free | $0 | 10 users, full REST API |
| All job APIs (Adzuna, Reed, Jooble, ATS boards, etc.) | $0 | Free tiers, see §4 |
| Electricity | ~$2–5/mo | Ollama embedding runs; rounding error |
| Disk | $0 | Bronze + landing under ~20 GB in year one |

**Hardware is the real question, and it's a sunk cost, not a running cost.**
Local embeddings want ≥16 GB RAM comfortably. A `bge-large` or
`nomic-embed-text` run over 40,000 job descriptions on CPU takes hours rather
than minutes; on Apple Silicon or any GPU it's fine. If your machine can't do
it, that pushes you to Vertex embeddings — see §3 for why that's a trap
rather than a cost.

**Local total: effectively $0/month.**

---

## 2. LLM API — the only line that moves

### Current rates (per 1M tokens)

| Model | Input | Output |
|---|---|---|
| Claude Haiku 4.5 | $1.00 | $5.00 |
| Claude Sonnet 5 | $2.00 | $10.00 |
| Claude Opus 5 | $5.00 | $25.00 |

<!-- Sonnet 5 is introductory pricing through 31 Aug 2026; standard is
     $3/$15 from 1 Sep. Batch API is 50% off. Cache hits are 10% of base
     input. Both discounts stack. -->

Two discounts do the heavy lifting: **Batch API halves everything** for
non-urgent work, and **cache hits cost 10% of base input** — which matters
enormously here, because your CV truth base and system prompts are the same
on every single call.

### Steady-state assumptions

| Assumption | Value |
|---|---|
| New deduplicated jobs ingested | 1,000/month |
| Jobs surviving hard filters to LLM re-rank | 200/month (50/week) |
| Applications submitted | 20/month |
| CV tailorings (incl. critic retries) | 40 generations/month |
| Manual pastes | 60/month |
| Eval harness runs | 4/month |

### Monthly steady state

| Component | Model | Vol | $/mo |
|---|---|---|---|
| Skill extraction from JDs | Haiku, batch | 1,000 | 1.26 |
| CV tailoring (2 critic loops) | Opus | 40 | 2.88 |
| Emergent cluster naming | Opus | 20 | 1.35 |
| Scoring re-rank | Sonnet | 200 | 1.35 |
| Eval harness runs | Sonnet, batch | 120 | 0.74 |
| Critic pass | Sonnet | 40 | 0.64 |
| Company research agent | Sonnet | 15 | 0.48 |
| Q&A refresh (quarterly, amortised) | Sonnet, batch | 53 | 0.47 |
| Cover letters | Sonnet | 20 | 0.33 |
| Elevator pitches | Sonnet | 20 | 0.25 |
| Manual-entry JD parsing | Haiku | 60 | 0.19 |
| Categorisation residual | Haiku, batch | 300 | 0.08 |
| **Total** | | | **~$10.00** |

A heavy month — 60 tailorings, 40 letters, an intensive application push —
comes to **~$15**.

### One-off, during the build

| Item | $ |
|---|---|
| Backfill skill extraction over ~8,000 collected jobs | 10.09 |
| Initial Q&A bank (480 questions) | 4.21 |
| Q&A critic pass | 1.88 |
| Golden set construction (Step 12a) | 1.38 |
| CV extraction + correction iterations | 0.88 |
| **Total** | **~$18.45** |

### The cost of getting routing wrong

Running everything on Opus with no caching and no batching:
**$39/month — 3.9× the disciplined figure.**

That's the whole cost story. Not infrastructure, not scale — model routing.
Haiku for extraction and classification, Sonnet for the bulk, Opus only for
tailoring and emergent-cluster naming where the quality gain is real and the
volume is low.


---

## 2a. Development configuration — ~$1–2/month

The plan builds against an LLM gateway with **per-task** provider resolution
(Step 1) and a prompt registry keyed on `(task, model_family)` (Step 12a).
That makes the development posture cheap without creating a migration cliff.

### What runs where during the build

| Task | Dev | Production target |
|---|---|---|
| Skill extraction, categorisation, JD parsing | local | **local — never migrates** |
| CV truth-base extraction | local | local (hand-corrected anyway) |
| Scoring re-rank | local | either — calibrate per provider |
| Q&A generation | local | local + review pass |
| Cover letters, pitches | local | Claude |
| CV tailoring | local | Claude |
| **Fabrication critic** | **Claude from day one** | Claude |
| Emergent cluster naming | local | Claude |

Most tasks never migrate. The high-volume mechanical work is genuinely fine
on a local model — which is the same conclusion as the $0 analysis below,
reached from the other direction.

### Development spend

| Item | $/mo |
|---|---|
| Weekly target-provider eval runs (~$1 per full run) | ~1.00 |
| Fabrication critic on target provider (~40 runs) | ~0.64 |
| Everything else | $0 — local |
| **Total during build** | **~$1.60** |

### Why not "build local, migrate at the end"

Because migration is not a config change. Prompts tuned for a small model are
wrong for a large one, and every calibration in the plan — Step 11a's
confidence cutoffs, Step 12a's eval thresholds, Step 16's scoring weights —
is model-specific and would need redoing. Worst of all, you would validate
the fabrication guard on one model and ship it running another.

Continuous dual-track costs ~$1.60/month and removes the migration entirely:
going live becomes flipping per-task config entries that already have tuned
prompts and passing evals behind them.

Full reasoning in `DECISIONS.md` §1.

### The genuine $0 configuration

If spend must be exactly zero: run every task local, drop GCP entirely
(host cron, local disk landing zone, no IAP because nothing is exposed), and
replace the automated fabrication critic with a **mandatory human diff review**
before any CV is exported. That is a workflow substitute, not a technical
one — the guard still exists, it is just you. Be explicit about that rather
than assuming a weak local critic is doing the job.

Hardware is the real constraint: 16 GB handles extraction and classification
well and generation poorly; 24–32 GB with a 20–30B MoE model handles
everything at acceptable quality. Free hosted API tiers are not a route here —
most reserve the right to train on inputs, and your inputs are your full
employment history plus third-party contact details.

---

## 3. GCP

### Configuration A — minimal (recommended)

| Service | Usage | $/mo |
|---|---|---|
| Cloud Run (API + Streamlit) | scale-to-zero, personal traffic | **$0** — inside free tier |
| Cloud Run Jobs (pipeline) | daily ~10 min, 1 vCPU / 2 GB | **$0** — inside free tier |
| Cloud Build | ~30 builds/mo | **$0** — 2,500 min/mo free |
| GCS landing bucket | ~5 GB standard, London | ~0.12 |
| Artifact Registry | 3 images, ~3 GB | ~0.25 |
| Secret Manager | ~8 secrets, low access | ~0.30 |
| Cloud Scheduler | 5 jobs (3 free) | ~0.20 |
| Egress | minimal | ~0.10 |
| **Neon Postgres** | free tier | **$0** |
| **Infra subtotal** | | **~$1** |
| LLM | | ~10 |
| **Total** | | **~$11/month** |

Cloud Run's free tier is generous enough that a single-user app with
scale-to-zero genuinely costs nothing. The pipeline job — ten minutes a day —
uses a rounding error's worth of the 180,000 free vCPU-seconds.

### Configuration B — full production

| Service | $/mo |
|---|---|
| Everything in Config A except the database | ~1 |
| **Cloud SQL** `db-f1-micro` + 10 GB SSD | ~12–15 |
| **Cloud SQL** `db-g1-small` + 10 GB SSD (more realistic) | ~28–35 |
| GCE `e2-small` for n8n (24×7) | ~13 |
| Load balancer, if IAP requires one | ~18 ⚠️ |
| **Infra subtotal** | **~$35–70** |
| LLM | ~10 |
| **Total** | **~$45–80/month** |

⚠️ **The IAP trap.** Identity-Aware Proxy itself is free, but historically it
required an external HTTPS load balancer at roughly $18/month — more than
your database. Direct IAP integration with Cloud Run has been available more
recently and would remove that cost entirely. **Verify which path applies
before you build Step 22**, because it's the difference between a $17 month
and a $45 one for the same functionality.

### Cost drivers, ranked

1. **Cloud SQL** — the single largest line, always-on, billed whether you use
   it or not. Start on Neon free.
2. **Load balancer for IAP** — potentially larger than the database. Verify.
3. **n8n VM** — always-on by necessity, since cron needs a persistent process.
   Avoidable: Cloud Scheduler handles the ingestion fan-out, and n8n only
   needs to exist for approval gates. Run it locally if you'd rather not pay.
4. **Everything else** — under $1 combined.

### Embeddings: the non-cost that's still a trap

Vertex `text-embedding-004` would cost cents per month at your volume — the
money is irrelevant. The problem is correctness: **local Ollama vectors and
Vertex vectors are not comparable**, so embedding locally and querying in
cloud silently corrupts every similarity score.

The plan's recommendation stands for reasons that have nothing to do with
price: embed locally, always, and let the GCP pipeline read precomputed
vectors.

---

## 4. Job data sources

All free at your volume.

| Source | Free tier | Risk of cost |
|---|---|---|
| Adzuna | ~1,000 calls/month | None — paid tiers exist but you won't need one |
| Reed | Free key | None |
| Jooble | Free key | None |
| Greenhouse / Lever / Ashby | Unlimited, keyless | None |
| Arbeitnow / Remotive / RemoteOK | Free | None |
| USAJOBS, EURES | Free | None |
| JobSpy | Free library | **Proxies** if you scale it — rotating residential proxies run $50–500/month and are the one place this project could get expensive |

**Adzuna's 1,000 calls/month is your real ingestion budget.** At 50 results
per call that's ~50,000 job-fetches monthly, which is comfortably more than
you need — but the discovery corpus (Step 4a) and the query matrix fan-out
(Step 22) both consume from it. Budget the matrix accordingly rather than
discovering the ceiling in production.

**On JobSpy:** keep it optional and low-volume. The moment you need proxies to
make it work, it has become the most expensive component in the system by an
order of magnitude, for data the aggregators mostly already gave you.

---

## 5. Cost by phase

| Phase | Duration | $/mo | Why |
|---|---|---|---|
| 0–2 (ingestion, dedup) | Weeks 1–6 | ~$1–3 | Local, almost no LLM. Step 12's GCP deploy is a few cents |
| 3 (CV, scoring) | Weeks 7–10 | ~$8–12 | LLM starts in earnest; golden set + backfill one-offs land here |
| 4–5 (artefacts, marts) | Weeks 11–16 | ~$12–18 | Q&A bank one-off ~$6 |
| 6 (production) | Ongoing | ~$11–17 | Config A steady state |
| Job-hunting peak | | ~$15–20 | 40–60 applications/month |

**Cumulative first six months: roughly $70–110** including one-offs, on
Config A.

---

## 6. Levers, in order of impact

1. **Model routing** — 3.9× swing. The single biggest lever, by a distance.
2. **Prompt caching** — your CV truth base and system prompts are identical
   on every call and cost 10% on cache hits. Worth ~40–60% of input spend on
   the generation features.
3. **Batch API** — 50% off. Everything non-interactive qualifies: skill
   extraction, categorisation, Q&A generation, eval runs.
4. **Neon over Cloud SQL** — saves $15–35/month for equivalent function at
   your scale.
5. **Embedding cache on content hash** (nice-to-have #8) — saves compute time
   rather than dollars locally, but becomes real money if you ever move
   embeddings to Vertex.
6. **LLM response caching** (nice-to-have #24) — regenerating the same
   artefact for the same job should be free.
7. **Run n8n locally** — saves $13/month. It only handles approval gates;
   there's no strong reason it must live in the cloud.

---

## 7. Budget guardrails

Nice-to-have **#9** (LLM cost accounting and hard caps) is the control here,
and it should be promoted before the first unattended scheduled generation
run rather than left in the backlog.

Suggested settings:

| Control | Value |
|---|---|
| Anthropic Console monthly spend limit | $30 |
| Per-run token cap in pipeline config | fail the run, don't truncate |
| Per-feature spend logged to `fct_llm_usage` | model, tokens, cost, prompt_version |
| GCP budget alert | £25/month, email at 50 / 90 / 100% |
| Adzuna call counter | alert at 800/1,000 |

Log `prompt_version` alongside cost. When spend jumps, you want to know
whether it was volume or a prompt that got longer — and those have completely
different fixes.

---

## 8. What could actually blow the budget

Ranked by likelihood × damage:

**1. A retry loop with no cap.** An agent looping on a failing critic pass,
unattended overnight, on Opus. This is the classic way personal AI projects
produce a surprise bill. The Step 17 design already caps the critic loop at
two iterations — keep it capped, and put a token ceiling on the run as well.

**2. Reprocessing bronze without a cache.** Re-running skill extraction over
40,000 jobs because a prompt changed: ~$50 each time, and you'll want to do it
more than once. The embedding and response caches turn this from painful to
free.

**3. Cloud SQL left running after you stop using it.** $30/month indefinitely
for a project you paused. Set the budget alert.

**4. JobSpy at volume.** Proxies are the only four-figure risk in this
entire system. Keep it pluggable and low-volume, exactly as planned.

**5. Emergent-detection LLM passes over the full title corpus.** Cluster
first, then send only cluster representatives to the model — never every
title. The plan already does this; it's worth stating because the naive
version costs 50× more for a worse result.

---

## 9. Comparison and sanity check

| What | $/mo |
|---|---|
| This system, Config A | ~11 |
| One premium job board subscription | 20–50 |
| One CV rewrite from a professional service | 100–400 one-off |
| One month of a commercial jobs API (JobsPipe-class) | 49+ |
| A Claude Pro subscription | 20 |

The economics are favourable, which is worth stating plainly: at ~$11/month
this costs less than a single job board subscription and less than a Claude
Pro plan. Even the full production configuration at ~$50 is roughly one hour
of your billable rate.

**The real cost of this project is your time, not your infrastructure** —
roughly 198 story points of it. Optimise the plan for what you'll actually
finish, not for what you'll spend.

---

## Verification checklist

Before committing to any configuration, confirm current figures for:

- [ ] Anthropic token rates and whether Sonnet 5 introductory pricing still applies
- [ ] Neon free-tier storage and compute limits against your projected bronze size
- [ ] Cloud Run free-tier allowances in `europe-west2`
- [ ] **Whether IAP on Cloud Run requires a load balancer** — the single
      largest swing factor in the GCP estimate
- [ ] Cloud SQL `db-f1-micro` vs `db-g1-small` pricing and pgvector support
- [ ] Adzuna free-tier call limit and whether it's per-key or per-account
