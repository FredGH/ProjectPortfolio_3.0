# Step 21b — Emergent role and macro trend detection: design

Status: draft for review · 2026-10-16 · PLAN.md "Step 21b"

## Purpose

Answer "what is the market becoming?" in two ways: **micro** (new titles and
functions forming inside or between the supported categories) and **macro**
(how the AI transformation changes what existing roles do). By design it works
on the **discovery corpus** and reaches outside the project's taxonomy.

The defining risk is confident nonsense. Every guard in this design exists to
keep a plausible-sounding but unreal "emergent role" from reaching you.

### Done when (this slice)

- The micro pipeline (canonical titles, novelty, clustering, hybridity, naming,
  review queue) runs end to end; below the maturity threshold the API and page
  refuse to show a novelty index and label what they do show as a preview.
- The four macro marts build and pass tests, with change metrics NULL until a
  comparable period exists.
- The plan's "three candidates, each corroborated or dismissed" cannot be met
  honestly yet (14 boards, one collection burst); the machinery is proven on
  synthetic data and runs on what exists. Promotion into the taxonomy and the
  forward-looking CV gap are a follow-up.

## Decisions taken in brainstorming

| Question | Decision |
|---|---|
| Scope | Micro and macro detection with the review queue; promotion and the forward CV gap deferred |
| Maturity | Gate it: config minimum history, periods and employer-breadth floor; below them no novelty index, only a labelled preview of clusters and hybridity |
| Corroboration | Left out: every cluster is an `uncorroborated` hypothesis, confirmable only by the user's explicit verdict and rationale |

## Corpus (dbt)

- `int_discovery_first_seen`: insert-only first-seen per discovery posting
  (`collection_channel = 'discovery'`, `entry_method = 'api'`), same pattern as
  `int_market_first_seen` (never full-refreshed).
- `discovery_job`: one row per deduplicated job delivered by the discovery
  channel: `job_group_id`, `first_seen_at`, `last_seen_at`, raw title, company,
  `normalised_company`, `is_agency` (same rule as Step 21a), `source_name`,
  `category`. Caveat: bronze keeps the channel of the last writer, so a posting
  later re-captured by a targeted run leaves the discovery corpus.

## Micro (Python, `core/emergent/`, schema `emergent`)

1. **Canonical titles** (`canonical.py`): lowercase; drop seniority/level words
   (config list); cut employer/location decoration after `-`, `|`, `,`, `(`;
   remove title-inflation words (stoplist: ninja, rockstar, guru, wizard, hero,
   jedi, evangelist...); collapse whitespace; a title whose remaining phrase is
   shorter than 2 characters is dropped. Agency postings are excluded BEFORE
   scoring. Output `emergent.title_canonical`: `canonical_title, first_seen_at,
   last_seen_at, distinct_employer_count` (non-agency), `distinct_source_count,
   postings, example_titles`.
2. **Novelty** (`novelty.py`): `recency x breadth x velocity`, each in [0,1].
   Breadth has a hard floor (`min_employers`, default 5): below it novelty is
   NULL ("noise"), not a small number. Velocity = growth of weekly distinct
   employers across the history; NULL without at least `min_periods` periods.
3. **Clustering** (`cluster.py`): embed canonical titles with the local Ollama
   model (cached in `emergent.title_embedding`, model recorded, derived and
   rebuildable); HDBSCAN (scikit-learn) with `min_cluster_size`; noise points
   stay noise. Each cluster stores its centroid, members, nearest category
   (centroids built from `category_seed_examples.yml`), the cosine to it and
   `is_candidate` (cosine below the threshold). Runs are versioned
   (`cluster_run_id`), never overwritten.
4. **Hybridity** (`hybrid.py`): per cluster centroid, best single-centroid cosine
   vs the best two-centroid blend (weights 0.1..0.9 in 0.1 steps over category
   pairs). Hybrid when the blend gains at least `hybrid_min_gain` and both
   weights are at least 0.25; stored as `(category_a, weight_a, category_b,
   weight_b, gain)`.
5. **Eligibility**: a cluster is a *candidate* only if it is outside every
   category centroid AND meets the breadth floor with non-agency employers AND
   survives the stoplist. Everything else is kept (for the preview) but never
   named.
6. **Maturity gate** (`maturity.py`): `status()` returns history days, distinct
   employers, distinct periods and `mature` (history >= `min_history_days`
   and periods >= `min_periods`). When not mature the API returns no novelty
   index and the page says exactly what is missing; clusters and hybridity are
   shown as "preview - history too short, not a finding".
7. **Naming** (`name.py`): on demand with a cost estimate and confirmation,
   Claude (`emergent_naming`, always Claude) names and characterises each
   eligible candidate from its member titles and top extracted skills.
   Stored in `emergent.role_candidate` with `corroboration = 'uncorroborated'`
   and the model/prompt version. Member titles are data, never instructions.
8. **Review** (`review.py`): verdict `confirmed | dismissed` with a required
   rationale in `emergent.emergent_role_review` (per user, RLS). A dismissal
   suppresses re-prompting for `dismiss_suppress_days`. Confirming records the
   verdict only; nothing is promoted into `category_map.yml` in this slice.

Config: `config/emergent.yml` (stoplist, level words, thresholds, maturity
window, suppression days).

## Macro (dbt, `gold`; targeted corpus, Step 21a panel and matrix stamps)

- `fct_ai_penetration`: category x month: share of postings mentioning each AI
  term (agents, RAG, evals, MCP, fine-tuning, vector stores, prompt
  engineering, LLM, generative AI) from the `ai_terms` seed, plus any-term
  share, with `n`.
- `fct_role_drift`: category x month x lookback (6 and 12 months): Jensen-Shannon
  divergence of the skill distribution now vs then. 0 for identical
  distributions, at most ln 2; NULL unless the compared months share a complete
  panel and matrix version.
- `fct_ai_skill_migration`: month: share of AI-skill mentions falling in
  categories other than `ai_ml_engineer` and `data_scientist`.
- `fct_displacement_language`: category x month: share of postings with
  oversight wording vs build wording (phrase seeds). A weak signal, labelled so.
- A per-job text-flag model `int_market_job_text_flags` carries the term and
  phrase flags so descriptions are not copied into every mart.

## Interfaces

- **CLI** (on demand, off the dashboard registry): `build-emergent-titles`,
  `cluster-emergent-roles`, `name-emergent-roles` (estimate first, `--yes`).
- **API** `/emergent/*`: status, clusters (preview flag), candidates, review
  queue and decision; the macro marts under `/market/macro/*`.
- **UI**: read-only **Emergent Roles** page (maturity box, preview clusters,
  named candidates, confirm/dismiss with rationale) and an "AI transformation"
  tab on **Market Marts**.

## Testing

Canonicalisation (stoplist, decoration); novelty math and the NULL below the
floor; hybridity on synthetic vectors (a 0.6/0.4 blend is found, a pure
category and an outlier are not); agency filtered before scoring; the maturity
gate refusing a novelty index; cluster-run versioning; naming with a fake
adapter and injection text in a member title; review decisions, suppression and
per-user isolation; JS-divergence properties and NULL gating in dbt; stamps.

## Out of scope

Promotion into the taxonomy (`category_map.yml` with `valid_from`, question
bank enqueue), the forward-looking CV gap view, web corroboration, Lever/Ashby
board ingestion, and scheduling (PLAN.md Step 22).

## Risks

- 14 Greenhouse boards cannot support the breadth floor: expect most clusters
  to be "noise" until more boards are added to the discovery channel.
- Local embeddings and HDBSCAN settings are starting values, not tuned.
- Displacement phrases are a weak proxy; the page says so.
