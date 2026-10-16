# Step 21a — Market intelligence marts: design

Status: draft for review · 2026-10-16 · PLAN.md "Step 21a"

## Purpose

Eight dbt marts, one per analytical grain, so the Step 21 dashboard is a thin
rendering layer. They must not let the *collection* masquerade as the
*market*: every trend is computed over a stable source panel, stamped with a
frozen query-matrix version, and every salary figure carries its `n` and
disclosure rate.

### Done when

All eight marts build and pass their tests; every trend metric respects the
source panel (a dbt test fails if one does not); every salary figure carries
`n` and `disclosure_rate`.

## Decisions taken in brainstorming

| Question | Decision |
|---|---|
| Trend date | **First seen by us** (earliest panel-source fetch), falling back to `posted_at` when missing; `posted_at` stays for age/lifecycle |
| `work_model` (remote/hybrid/onsite) | Deterministic keyword rules on title/location/description; ambiguous stays `unknown`; an LLM could fill the same column later |
| Scope | All eight marts, trend marts built correctly but honestly sparse (history is 3-4 September 2026 only) |

## Inputs (dbt seeds, versioned in git)

- **`dbt/seeds/source_panel.csv`**: `source_name, active_from`. Only real
  sources (adzuna, reed, greenhouse, jooble). Every market model joins to it,
  which also excludes the `test_source_*` rows and manual entries.
- **`dbt/seeds/query_matrix_versions.csv`**: `matrix_version, effective_from,
  note`. Starts at `v1`. Changing the keyword x region matrix means adding a
  row. A trend row is stamped with the version in force for its period, and
  `trend_break` is true when it differs from the previous period's.

## Shared base: `intermediate/int_market_job`

One row per `job_group_id` from `gold.dim_job`, `entry_method = 'api'`, with at
least one panel source. Columns: `job_group_id`, `first_seen_at` (earliest
`first_seen_at` among its panel sources, else `posted_at`), `last_seen_at`
(latest bronze `fetched_at` among its panel sources), `posted_at`, `category`,
`seniority_band`, `region`, `normalised_company`, `contract_type`
(`permanent` | `contract` | `unknown` from `engagement_type`: contract, interim
and ftc are contract), `annual_salary` (permanent, from `rate_annualised`),
`day_rate` (contract, from `rate_daily_equivalent`), `is_agency`, `work_model`,
`panel_sources` (the job's panel sources). Window helpers: `first_seen_week`,
`first_seen_month`.

- **`work_model`**: a macro of ordered rules: explicit onsite ("fully on-site",
  "office-based", "5 days in the office"), hybrid ("hybrid", "x days a week in
  the office"), remote ("fully remote", "remote-first", "work from home", "100%
  remote"); first match wins, text is title + location + description; otherwise
  `unknown`. Tested on example phrases.
- **`is_agency`**: company-name patterns (recruitment, staffing, resourcing,
  talent, search, consulting... in a seed `agency_name_patterns.csv`); an
  existing Step 19a resolution (`intel.job_company.poster_kind = 'agency'`)
  wins when present.

## The panel and matrix rule (as built)

A source is *fully active* in a period when its `active_from` is on or before
the period start; a period is `panel_complete` when every seed source is. Two
rules, enforced in `int_market_job` and `int_market_period_stamp`:

1. **Who counts.** Only jobs delivered by a seed-panel source exist in any
   market mart (manual entries and the `test_source_*` rows never do).
2. **What may be compared.** Level metrics (counts, medians, shares) are always
   published, stamped with `panel_complete`, `panel_sources`, `matrix_version`
   and `trend_break`, so charts footnote them. A *change* metric (WoW, MoM,
   `rank_delta_90d`, `is_new_entrant`) is published only when the period and its
   comparison period are both `panel_complete`, have the same `panel_sources`
   and the same `matrix_version`; otherwise it is NULL, never 0.

Ruling: the first week/month of collection is `panel_complete = false` (the
sources began mid-period), so level metrics appear but no change metric does.
Hiding the levels as well would leave the cross-sectional views the plan says
must work "from day one" empty.

## The eight marts (schema `gold`)

1. `fct_market_volume`: category x region x week x seniority x work_model:
   postings, WoW/MoM change, share of category total, new vs reposted (a group
   is reposted when a later duplicate source first-seen is >14 days after the
   earliest). Trend stamps.
2. `fct_market_salary`: category x region x month x contract_type:
   `n_postings`, `n_disclosed`, `disclosure_rate`, median/P25/P75/IQR/min/max
   (day rate for contract, annual for permanent, never mixed). Statistics are
   published only when `n_disclosed >= 5`; `n` and the rate always are.
3. `fct_skill_demand`: category x region x month x skill: frequency, share of
   category postings, rank, `rank_delta_90d` (NULL without history),
   must-have vs nice-to-have ratio. Trend stamps.
4. `fct_skill_salary_premium`: category x contract_type: median with the skill
   minus without; `n_with`, `n_without`; NULL premium unless both >= 5.
5. `fct_skill_cooccurrence`: skill pairs per category: support, lift; minimum
   support 3.
6. `fct_employer_activity`: company x category x month: volume, agency vs
   direct, `first_seen_month` / new-entrant flag. Trend stamps.
7. `fct_posting_lifecycle`: per job: days between `first_seen_at` and
   `last_seen_at`; `still_live` (still in its sources' latest fetch run);
   live jobs are censored (duration NULL), never given a fake length.
8. `fct_market_composition`: work_model, contract-vs-permanent and seniority
   shares by category x region x month. Trend stamps.

The existing `fct_market_demand` is unchanged.

## Testing

- Primary-key/uniqueness and not-null tests on each mart's grain.
- **Singular tests**: no trend metric includes a source outside the panel for
  its period; every salary row carries `n_postings`, `n_disclosed` and
  `disclosure_rate` with `n_disclosed <= n_postings`; shares sum to 1; no
  statistic is published below the minimum `n`; change metrics are NULL when
  `trend_break` or the panel changed; manual and test sources never appear.
- `work_model` and `is_agency` rules tested on example phrases/names (a seed of
  cases and a singular test comparing expected to derived).
- All run with `dbt test` on the dev Postgres.

## Out of scope

The Streamlit dashboard (Step 21), seeding the Step 4 target-company registry
from `fct_employer_activity`, an LLM work-model classifier, historical Adzuna
salary backfill, and any change to ingestion.

## Plan changes

`PLAN.md` Step 21a: note the first-seen trend date, the two seeds
(`source_panel`, `query_matrix_versions`), the censored lifecycle, and the
minimum-n rules.

## Risks and open points

- With one collection burst, trend marts are mostly NULL change columns; that is
  correct, and they become informative as history accrues.
- Keyword `work_model` rules will leave many postings `unknown`; the share is
  itself reported in `fct_market_composition`.
- `last_seen_at` is only as good as re-fetch frequency; one fetch means
  lifecycle durations are zero and `still_live` is the useful field until
  collection repeats.
