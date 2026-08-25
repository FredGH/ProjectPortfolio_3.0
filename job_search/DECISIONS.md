# Decisions

Standing decisions for the Job Search Platform, with the reasoning that
produced them. Companion to `PLAN.md`, `backlog.yml` and `COSTS.md`.

Each entry records what was decided, what was rejected, and — most
importantly — **what would change the decision**. When you revisit one of
these in month six, that last field is the one that matters.

---

## 1. Dual-track LLM providers, no migration step

**Decided:** 23 August 2026
**Status:** Accepted
**Affects:** Step 1, Step 12a, Step 17

### The question

Should development run entirely on local models against a provider
abstraction, with a final migration step to Anthropic when the system goes
into real use?

### Decision

**The abstraction, yes. The migration step, no.** Build against a
provider-agnostic gateway from Step 1, but run a continuous dual track — local
for daily iteration, target provider validated continuously — rather than
deferring the provider switch to a single step at the end.

### Why the big-bang migration fails

**Prompts are model-specific artefacts, not portable config.** Small models
need verbose scaffolding, explicit step-by-step decomposition, and heavy
few-shot examples to hold a task together. Those same techniques frequently
*degrade* output on a stronger model, because they constrain it into the
shape of the weaker one. Prompt work tuned over weeks on a 20B local model is
largely discarded on migration — you are not converting prompts, you are
rewriting them.

**Every calibration in the plan is provider-specific.** Step 11a's
categorisation confidence cutoffs, Step 12a's eval thresholds, Step 16's
scoring weights fitted against re-rank output. A provider swap invalidates
all three, and two of them are on the critical-risk list. "Migration" would
silently mean redoing the calibration work that the plan already identifies
as the hardest to get right.

**The fabrication guard is the dangerous case.** Validating the Step 17
critic against a local model and swapping at the end ships a safety guard
that has never been tested on the model actually running it. The failure mode
is false confidence: a guard that passed its tests and does not do its job.
The reverse also bites — local models are weaker at strict schema adherence,
so you would build regex fallbacks and defensive parsing that becomes either
dead weight or a mask over real failures.

**The timing is worst-case.** Migration lands exactly when you want to start
using the system for real applications — the moment you least want to be
debugging output quality.

### What was implemented instead

Three structural decisions, none expensive:

**1. Per-task provider resolution, not a global switch.**

```python
llm.complete(task="skill_extraction", ...)
```

Model resolves from a config table keyed on task. A single global
`LLM_PROVIDER` is precisely what forces an all-or-nothing migration; per-task
routing lets the boundary move one task at a time. Built in Step 1, before
any LLM code exists.

**2. Prompt registry keyed on `(task, model_family)`.**

```
prompts/skill_extraction/claude.v3.md
prompts/skill_extraction/local.v7.md
```

Never convert a prompt between families. Write the target-family variant when
ready; keep both. There is no migration because nothing has to be converted.

**3. The eval harness runs both providers.**

Definition of done for every LLM step is **"passes evals on the target
provider"**, even though daily iteration runs local. Calibration values are
recorded per provider rather than once globally. A full target-provider eval
run costs roughly $1, so weekly in CI is trivial insurance.

### The task split

| Task | Dev | Target |
|---|---|---|
| Skill extraction, categorisation, JD parsing | local | **local — never migrates** |
| CV truth-base extraction | local | local (hand-corrected anyway) |
| Scoring re-rank | local | either, calibrated per provider |
| Q&A generation | local | local + review pass |
| Cover letters, pitches | local | Claude |
| CV tailoring | local | Claude |
| **Fabrication critic** | **Claude from day one** | Claude |
| Emergent cluster naming | local | Claude |

Most tasks never migrate at all. The high-volume mechanical work is genuinely
fine on a local model — the same conclusion the $0-cost analysis reaches from
the opposite direction.

**The critic is the deliberate exception.** It runs on the target provider
from day one even while the Tailor runs local, because a guard validated on a
weaker model than the one running it is worse than no guard. Under $1/month
at 40 runs. Asserted in tests so config drift cannot silently downgrade it.

### Consequences

- Development costs ~$1.60/month rather than $0 — eval runs plus the critic
- Local-speed iteration is preserved for everything else
- "Going live" is flipping per-task config entries that already have tuned
  prompts and passing evals behind them. A config change, genuinely
- Two prompt variants per generation task must be maintained. Accepted:
  cheaper than one rewrite plus three recalibrations
- The quality gap between providers is visible continuously rather than
  discovered at the end

### Rejected alternatives

**Build local, migrate at the end** — the proposal this decision responds to.
Rejected for the four reasons above. The abstraction it assumes is right; the
deferral is not.

**Build directly on Anthropic throughout** — simpler, and roughly $10/month
through a build phase where most calls are throwaway iterations. Rejected as
unnecessary spend for no quality benefit on mechanical tasks that ship local
anyway.

**Free hosted API tiers (Google AI Studio, Groq, Mistral, OpenRouter)** —
genuinely free at this volume. Rejected on privacy: most free tiers reserve
the right to train on inputs, and the inputs here are a full employment
history, salary expectations, and third-party contact details gathered during
company research. Unacceptable for anything touching the truth base. They are
fine for JD skill extraction on public text — but that is exactly the task
local models handle well, so the tier buys nothing.

### What would change this

- A local model that passes the fabrication-guard evals at target-provider
  quality — then the critic moves local and the target-provider spend drops
  to eval runs alone
- Target-provider pricing falling far enough that local iteration saves
  nothing meaningful — then collapse to a single track and delete the local
  prompt family
- Hardware that cannot run a 20B-class model — then the dev track moves to
  the target provider and the build phase costs ~$10/month instead of ~$1.60

---

## 2. Standing decisions carried from the plan

Recorded here so they are findable when questioned. Full reasoning lives in
the referenced `PLAN.md` sections.

| # | Decision | Rejected | Why | Ref |
|---|---|---|---|---|
| 2.1 | Aggregator-first sources; LinkedIn via manual paste only | LinkedIn API, Voyager, RapidAPI resellers | No usable LinkedIn jobs API exists; unofficial routes get accounts banned | Step 4 |
| 2.2 | Postgres + dbt medallion with a separate landing zone | DuckDB | n8n concurrency needs a server DB; landing zone makes bronze rebuildable without re-hitting APIs | Step 2 |
| 2.3 | Streamlit as the single UI | Gradio, or both | Multi-panel analytical app, not function-shaped. Portability comes from Docker, not the framework | Step 21 |
| 2.4 | FastAPI holds all logic; Streamlit and n8n are clients | Logic in Streamlit | n8n needs endpoints for approval gates. One interface, two clients | Step 21 |
| 2.5 | Dedup precision > recall, threshold from a PR curve | Intuitive threshold | A false merge silently loses a job forever; a false split is merely annoying | Step 9 |
| 2.6 | `job_group_id` immutable once assigned | Re-clustering each run | Artefacts and application history reference it; a shifting key orphans everything | Step 10 |
| 2.7 | Field-level survivorship, `description` and `apply_url` on separate rules | Record-level winner | Manual entries have the fullest text; ATS sources have the right apply link | Step 10 |
| 2.8 | Embed locally with Ollama, always | Vertex embeddings in GCP | Different models produce incompatible vectors; mixing corrupts similarity silently | Step 12 |
| 2.9 | Neon free tier over Cloud SQL initially | Cloud SQL from the start | £20–30/month always-on for equivalent function at this scale | Step 12 |
| 2.10 | Generation constrained to the truth base with an evidence-ref critic | Prompt-level instruction not to exaggerate | The difference between a tailoring tool and a fabrication engine | Step 17 |
| 2.11 | ATS rules enforced in a locked template, not by prompting | Prompt-level ATS instructions | A template without table support works every time; a prompt works most of the time | Step 18 |
| 2.12 | Two corpora — targeted (frozen matrix) and discovery (broad) | One collection channel | You cannot discover a title you did not think to search for | Step 4a |
| 2.13 | `unknown` is a value for IR35 and engagement type; never defaulted | Default to most likely | Defaulting outside-IR35 recommends roles with a large net pay cut | Step 5a |
| 2.14 | Emergent-role output is hypothesis, not finding | Auto-publish clusters | Clustering plus LLM naming always produces plausible roles whether or not any exist | Step 21b |
| 2.15 | Jira Free, one tool for both project and bug tracking | Monday.com; Jira + separate bug tracker | Monday free caps at 200 items account-wide; this backlog is ~350 | Step 0 |
| 2.16 | One-way Jira sync with disjoint field ownership | Bidirectional sync | Removes conflict resolution entirely; sync is where solo projects die | Step 0 |
| 2.17 | Cloud Scheduler owns cron; n8n is webhook-only | n8n cron on Cloud Run | Scale-to-zero kills cron triggers silently | Step 23 |
| 2.18 | Serial implementation, parallel agents deferred | Multi-agent parallel build | Review bandwidth is the binding constraint on a solo project, not agent capacity | — |

---

## 3. No LLM orchestration framework

**Decided:** 23 August 2026
**Status:** Accepted
**Affects:** Steps 13, 15, 17, 19a

### Decision

**No LangChain, LlamaIndex, or Haystack as an architectural layer.** Direct
provider SDK plus Pydantic. Individual utilities from these ecosystems are
used as libraries where they earn their place.

### Why

The LLM surface in this system is narrow and well-shaped: structured
extraction against Pydantic schemas, a bounded two-iteration critic loop, and
a batch generation job. There is no open-ended agent planning, no dynamic tool
selection, no multi-hop retrieval chain — the places where an orchestration
framework genuinely pays for itself.

Direct SDK covers all of it in less code than the framework's configuration
would take. More importantly it stays **debuggable**, and Step 12a's entire
premise is being able to trace a quality regression to a specific change. A
framework's abstraction layers sit directly between you and that.

The frameworks also move fast and break interfaces. This project has a
multi-month build and a multi-year useful life; the provider SDK is a more
stable dependency than the wrapper around it.

### What IS used, as libraries

| Tool | Used for | Not used for |
|---|---|---|
| **Docling** | CV and document extraction (Step 13) | anything else |
| **Crawl4AI / Firecrawl** | Company research page fetching (Step 19a) | general orchestration |
| **LlamaIndex node parsers** | Structural chunking of job specs (Step 15) | retrieval, agents, or query engines |
| **RAGAS** | Faithfulness metric in the tailoring evals (Step 12a) | as an eval framework wholesale |
| **sentence-transformers / bge-reranker** | Cross-encoder reranking (Step 15) | — |

The distinction is deliberate: import a function, not an architecture.

### Consequences

- More code written by hand — accepted, and it is not much
- No community-standard patterns to copy from — accepted, the patterns here
  are unusual anyway (constrained generation against a truth base is not a
  common RAG shape)
- Full visibility into every prompt and every call, which the eval harness
  depends on
- Provider swap is handled by the Step 1 gateway, not by a framework's
  abstraction — see §1

### What would change this

- The agent surface growing well beyond the four agents in the plan
  (Researcher, Tailor, Critic, Interviewer) into genuine dynamic planning
- A need for multi-hop retrieval that the structural-chunk approach cannot
  serve
- Framework-native evaluation tooling becoming materially better than the
  custom harness in Step 12a

---

## 4. Retrieval design

**Decided:** 23 August 2026
**Status:** Accepted
**Affects:** Step 15

| Decision | Rejected | Why |
|---|---|---|
| Structural chunking by JD section | Fixed-size token windows | Chunking quality dominates retrieval quality more than model choice; a fixed window blends benefits text into a requirements comparison |
| Like-for-like section comparison | Whole-document similarity | Experience vs responsibilities and skills vs requirements are the meaningful pairings |
| Cross-encoder rerank before the LLM stage | Bi-encoder cosine straight into LLM re-rank | Cross-encoders read both texts together and are materially better at pairwise relevance; local, free, seconds |
| Embedding dimension and index type fixed at Step 15 | Deferred to nice-to-have | Dimension is baked in at first embedding; changing it means re-embedding everything |
| pgvector | Chroma, Pinecone, Qdrant | Vectors must join against `dim_job` and the marts. A separate vector store means a distributed join for every query |

---

## 5. Job title mirroring across generated artefacts

**Decided:** 23 August 2026
**Status:** Accepted
**Affects:** Steps 6, 10, 17, 18, 19

### Decision

Every document generated for a job — CV, cover letter, elevator pitch, and
anything added later — carries the **target job title exactly as the posting
states it**, injected as a template field rather than generated.

### Three title fields, three purposes

| Field | Content | Used for |
|---|---|---|
| `title_raw` | Source title, verbatim | Audit, provenance |
| `strip_title` output | Seniority prefixes, `(m/f/d)`, req IDs, `- Remote` removed | **Matching and dedup only** |
| `title_for_display` | Decoration stripped, **seniority and qualifiers kept** | **All generated documents** |

The trap this prevents: `strip_title` deliberately removes "Senior" so that
"Senior Data Engineer" and "Data Engineer" block together for dedup. A CV
headline built from that output would understate the role you're applying
for. Same normaliser, wrong output — hence a separate field rather than a
reused one.

### Which title survives a dedup merge

`title_for_display` survives from **the same source as `apply_url`**. Boards
phrase the same role differently, and the document must mirror the posting
you are actually applying through — not whichever source happened to have the
longest description. This is a third field-level survivorship rule alongside
`description` and `apply_url` (see §2.7).

### Enforcement is mechanical

Template field injection, not a prompt instruction. Same reasoning as the ATS
rules in §2.11: a prompt asking for the exact title works most of the time; a
template field works every time. Asserted against the **rendered** document
text, not the source data, so a template change cannot silently drop it.

Filenames follow `<surname>_<title_for_display>_<company>.docx`. Recruiters
sort and search by filename.

### The boundary — headline mirrors, history does not

**This is the part that matters.**

Putting the target title in the CV headline is normal positioning: it states
what you are applying for. **Altering a past job title in the experience
section to match the spec is fabricating employment history**, and it is the
kind of thing that ends an offer at reference-check stage.

The Step 17 critic enforces both halves:

1. The exact `title_for_display` string appears in the headline
2. **No experience-section title differs from the truth base**

The second assertion is the important one, and it is easy to omit because the
first is what the requirement sounds like.

Where the target title implies seniority or scope the truth base does not
evidence — a "Head of Data" posting against an individual-contributor history
— flag as **stretch**, not fabrication. The headline remains honest about
intent; you simply want to see it before sending.

### What would change this

- Evidence from the outcome loop (nice-to-have #1) that exact-title headlines
  underperform a role-family headline for ATS ranking. Measurable once ~30
  outcomes exist

---

## 6. Two taxonomies, user-gated growth

**Decided:** 23 August 2026
**Status:** Accepted
**Affects:** Steps 11a, 20, 21b, 21

### Decision

Separate the **classification taxonomy** (7 values, analytical grain) from
**`qa_category`** (4 active, question-bank grain), mapped in config. Confirmed
emergent roles may grow either or both, but only via an explicit user prompt
after a statistical confirmation gate.

### The mapping

| Classification (7) | → `qa_category` (4) |
|---|---|
| software engineer | software engineer |
| data engineer | data engineer |
| data scientist | data scientist |
| AI/ML engineer | AI engineer |
| analytics engineer | data engineer |
| platform/DevOps | software engineer |
| other | — (no bank) |

### Why two grains rather than one

They optimise for opposite things. Market analysis wants **fine** grain:
analytics engineer and data engineer have materially different salary curves
and skill signatures, and merging them destroys real signal in
`fct_market_salary` and `fct_skill_demand`. The question bank wants **coarse**
grain: 30 questions per topic per category is expensive to generate and
expensive to review, the behavioural half transfers completely between
adjacent categories, and the technical half overlaps substantially.

Forcing one taxonomy to serve both means either paying for question banks you
will never interview against, or losing analytical resolution you need.

### Why config, not code

`category_map.yml` holds both the active `qa_category` list and the mapping.
Step 21b is explicitly designed to propose extending it, so a code change per
extension would make the feature unusable in practice. Question bank
generation is **idempotent per category** — promoting one generates only that
bank — which is what makes promotion cheap enough to accept.

### Promotion: gated, then prompted

**Statistical gate first** — employer-breadth floor, sustained across N
consecutive periods, non-negative growth, agency-filtered. Only candidates
clearing all four ever reach the user.

This matters because the feature's failure mode is confident nonsense. A
prompt is an interruption; an interruption you learn to dismiss is worse than
no prompt at all. The gate protects the prompt's credibility.

**Then a four-way choice:** add as classification category, add as
`qa_category`, both, or dismiss. Dismissal is recorded and suppresses
re-prompting for that cluster for a configured period.

### Never auto-promote

A taxonomy that changes without consent **silently invalidates every
historical chart**. Last quarter's "data engineer" volume means something
different once part of it reclassifies to a new category, and nothing in the
chart tells you that happened.

Accepted promotions append to `category_map.yml` with a `valid_from` stamp,
so historical classification remains reproducible. Retro-classifying existing
jobs is an explicit backfill decision, never a side effect of promotion.

### Consequences

- Analytics engineer and platform/DevOps roles are fully classified, counted
  and scored, but interview against an adjacent bank. Acceptable — the
  overlap is high
- Adding the fifth bank costs one generation run, not a refactor
- Charts spanning a promotion boundary need the `valid_from` caveat surfaced
  in the UI

### What would change this

- Interviewing repeatedly in a mapped category and finding the adjacent bank
  inadequate — promote it to its own `qa_category`, which is exactly what the
  mechanism is for
- The classification taxonomy growing past ~12, at which point maintaining
  two lists costs more than it saves

---

## 7. Multi-user tenancy

**Decided:** 23 August 2026
**Status:** Accepted
**Affects:** Step 1a, 13, 15, 16, 17, 20, 20a, 21, 21c, 22a, 23a

### Decision

Multi-user from the schema up, governed by a **two-zone split**: job data is
shared, anything derived from a person is per-user. Exactly one base CV per
user, enforced by a database constraint. Tenancy established in Phase 0, auth
and data-protection obligations in Phase 6.

### The two-zone split

| Shared — collected once | Per-user — scoped to `user_id` |
|---|---|
| Job postings, bronze, dedup identity map | CV truth base (one per user) |
| `dim_job`, `dim_company`, company intel | `fct_job_score`, calibration weights |
| All market marts, taxonomy, emergent detection | All generated artefacts |
| Question text and model answers | Personal answers, SM-2 progress |
| | Applications, offers, alerts, preferences |

This is what keeps multi-user economically viable. Ingestion, dedup, skill
extraction, categorisation and the whole market intelligence layer are the
high-volume LLM work, and they **amortise across users** rather than
multiplying. Only tailoring, letters and scoring scale per head.

Phases 0–2 barely change. Phases 3–5 are where tenancy bites.

### One base CV per user

`UNIQUE` constraint on `user_id` in `cv_truth_base`. A schema guarantee, not
application logic — logic gets bypassed by import paths and test fixtures.
Replacement is a versioned update, never a second row, so "which CV produced
this artefact?" stays answerable.

### Isolation is defence in depth

Row-level security on every per-user table, session user set from a verified
token only, separate app and migration DB roles, and a negative test per
table asserting a `WHERE`-less query returns nothing belonging to another
user.

RLS is not a substitute for correct queries. It is the backstop for the one
query that forgets, and given the contents — employment history, salary
expectations, application outcomes — a single leak is not a bug you apologise
for.

### Scope: two trusted users, personal use

**Two users, known to each other, using the tool for their own job hunting.**
Not published, not commercial, no public sign-up. That scope drives two
scoping decisions:

**Statutory data protection is out of scope.** UK GDPR's
household/personal-use exemption covers activity of this shape — personal
job hunting, closed circle, no commercial purpose. Building export flows,
retention jobs, processor agreements and audit logs for a counterparty who
is sitting next to you is ceremony, not protection. The full scope is
recorded as **LATER item #1** with an explicit trigger rather than deleted,
because the exemption stops applying the moment the scope changes and by then
the data is already collected. (Not legal advice — the ordinary reading.)

**Isolation stays, reframed.** Row-level security is retained not as a
privacy control between trusted people but as a **correctness** guard. The
realistic failure is a query missing its `WHERE user_id` and blending two
users' scores into one dashboard, or an `evidence_ref` from the wrong truth
base landing in a tailored CV. Wrong-output bugs, hard to spot, cheap to
prevent at Step 1a.

Authentication shrinks accordingly: IAP already fronts the UI, so allowlist
two accounts and read the identity it provides. Step 22a goes from 8 points
to 3, and no OIDC implementation is needed.

### The costs that remain

1. **Financial.** LLM cost was ~$10/month for one user. Roughly $6–7 of that
   scales per head; ~$3 is shared work that amortises. Two users lands near
   $17/month. Still small, but per-user quotas and a fair-use split on the
   Adzuna allowance are worth the few lines — the free tiers were sized for
   one person.
2. **Operational.** A second person depending on it means you can't leave it
   broken for a fortnight. Mild at two users, real nonetheless.

### Rejected alternatives

**Single-user, run separate instances per person** — no shared job pool, so
collection cost and API quota multiply per user, and the market marts lose
the statistical base that makes Step 21b work at all. Simpler, materially
worse.

**Multi-user later** — rejected outright. Retrofitting `user_id`, indexes,
RLS and dbt grain across 30+ tables is a rewrite. The convention must precede
the first table.

### What would change this

- **A third user outside the trusted pair, any commercial or client use, or
  public sign-up including a portfolio demo people can register for.** Any of
  these ends the household exemption and promotes LATER #1 into the plan —
  8 points, plus terms of service, a support channel and an availability
  commitment
- Staying single-user: Step 1a shrinks to a stub and Step 22a disappears

---

## 8. Open questions

Not yet decided. Each needs an answer before the step that depends on it.

| Question | Needed by | Note |
|---|---|---|
| May third-party contact details (recruiters, interviewers) be stored at all? | Step 20a | They never consented. Roles and dates may be sufficient, and storing less is simpler than governing more |
| Does IAP on Cloud Run require a load balancer? | Step 22 | ~$18/month swing — larger than the database |
| Neon free-tier limits vs projected bronze size | Step 12 | Determines whether Cloud SQL becomes necessary |
| Local hardware capacity for a 20B-class model | Step 12a | If insufficient, dev track moves to the target provider (~$10/mo) |
| Break the `STEP-12 → STEP-13` link for a parallel CV track? | Phase 3 | Phase 3 has no real dependency on Phases 1–2 |
| Scope French-market support in or out | Step 13 | Currently ambiguous; ESCO is multilingual but CV conventions differ |
| Adzuna call budget allocation across targeted vs discovery | Step 4a | 1,000 calls/month is the real ingestion ceiling |
