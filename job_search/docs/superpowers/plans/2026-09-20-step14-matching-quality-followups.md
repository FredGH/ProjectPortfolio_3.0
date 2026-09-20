# Step 14 follow-up — matching quality, throughput and hardening

**Status: agreed plan, decisions recorded 2026-09-20 (section 5). Nothing in this
document is implemented.** It records what the first real-data run of Step 14
(2026-09-20) showed, and the steps to fix it. Each work item below becomes its own
spec/plan/PR when it is picked up.

Context: Step 14 (PR #20) shipped the ESCO vocabulary, mapper, review flow and CV/JD
mapping (`docs/esco.md`, spec `2026-09-19-step14-esco-skill-normalisation-design.md`).

## 1. Evidence — first real-data run

Run against the real dev DB: ESCO v1.2.1 loaded (13,939 skills, 101,133 labels) and
embedded; skills extracted from **5** random Greenhouse jobs; the one stored CV (40 skills)
mapped as a new version.

| Measure | Result |
|---|---|
| Job skill strings mapped | 14 of 64 distinct (22%) |
| CV skills mapped | 10 of 40 (25%) |
| `skill_mapping` rows by method | none/open 80, label 17, embedding 3, alias 3 |
| Bridge rows / jobs | 21 rows over 5 jobs; dbt tests 13/13 pass on real data |
| Must-have coverage of those jobs by the CV | 1/6, 1/3, 1/4, 1/1, 0/5 |
| Extraction speed (local llama3.1:8b, CPU) | ~152 s per job (≈2 LLM calls each) |
| Jobs waiting | 2,122 Greenhouse survivors with a description (2,117 not yet extracted) ≈ **90 hours** if all extracted |

Failure modes seen (real examples):

- **F1 — ESCO lacks most modern tools.** Unmatched: dbt, Docker, Snowflake, Terraform,
  Kubernetes, Redis, DynamoDB, Datadog, OpenSearch, Looker, Pandas, PySpark, Streamlit,
  Great Expectations, Dagster, Go, Rust, Elixir, CI/CD. Nearest ESCO candidates were
  nonsense (`terraform`→"pour terrazzo", `rust`→"remove rust from motor vehicles").
- **F2 — compound strings never match.** "AWS (S3, ECS/Fargate, Lambda)", "CI/CD
  (Jira+Git+Terraform)", "Hadoop (Hive/Impala)", "TypeScript/React", "MySQL (RDS)". The
  normaliser leaves `(rds` (unbalanced) and does not strip qualifiers or split.
- **F3 — wrong exact-label matches, invisible in the UI.** `visual studio`→"Visual Basic",
  `kotlin`→"computer programming", `numpy`/`scikit-learn`→"software components libraries".
  ESCO lists tools as hidden labels under broad skills. The Skill Review verify tab shows
  only *embedding* matches, so these can only be fixed by an alias.
- **F4 — corrections don't re-apply.** `--remap-unresolved` deletes only embedding and
  open rows, so an alias added later does not change an existing wrong `label` row, and
  `map-cv-skills` never overwrites an existing id, so a wrong CV id sticks.
- **F5 — extraction is far too slow to run at scale**, and it also processes ~23 junk test
  rows (`test_source*`, left over from earlier tests) and 476 snippet-only rows
  (Adzuna/Jooble/Reed, 300–500 chars, too short to yield many skills).
- **F6 — smaller items:** the 0.85 threshold looks right (near-misses below it were mostly
  wrong; the few right ones were close: `mysql` 0.836); 21 CSV rows share a concept id with
  another row; `extract-job-skills` exits 0 even if every job failed.

## 2. Goals and success measures

Goals: raise the share of *correctly* mapped skills, make wrong matches findable and
fixable in the UI, make corrections re-apply, and make extraction runnable on a sensible
scope. Non-goals: Step 15 scoring, authentication (Step 22a), changing the
local-only rule for skill extraction (DECISIONS §1).

Proposed measures (to agree, section 5-D1), on a **frozen baseline** — the same 5 jobs and
the CV's 40 skills — before and after:

- mapped share ≥ 60% of CV skills and ≥ 50% of job strings;
- 0 known-wrong matches in a hand-checked list of every mapped string;
- every remaining unmapped string is either a genuine long-tail term or queued for review.

Capture the baseline **before** any code lands (W0).

## 3. Work items

Ordered by value/risk. "Files" are the expected touch points.

### W0 — Freeze the baseline (no code)
Export current `silver.skill_mapping` (with matched labels), the CV's skill/id list and the
per-job coverage table to a CSV outside the repo. *Done when:* the files exist and are
referenced in the PRs' test plans.

### W1 — Mapper fallback forms for compound strings (fixes F2)
**Decision (recommended): do not change `normalise_skill`.** Its output is a persisted key
in 101k `esco.skill_label` rows, `skill_alias`, `skill_mapping` and `job_skill_raw`; changing
it means re-keying everything. Instead add a pure `candidate_forms(raw)` and let the mapper
try, in order: the whole normalised string → the head with parenthetical qualifiers removed
("mysql (rds)" → "mysql") → (JD side only, W1b) each `/`- or `,`-separated part.
Whole-string first keeps genuine slash terms whole (`ci/cd`, `a/b testing`, `pl/sql`).
- **W1a** Head-only fallback; one id per string. Files: `core/skills/normalise.py`
  (new function), `core/skills/mapper.py` (`map_skill`), `docs/esco.md`. Tests: unit cases
  for the forms (unbalanced/nested parens, commas, slashes, empty results); integration:
  the F2 examples map via alias/label on the head; existing mapping keys unchanged.
- **W1b (later, optional)** Multi-skill expansion for JD strings ("TypeScript/React" →
  two skills). Needs a new table (`silver.skill_mapping_part`) and a bridge change; only do
  it if W1a leaves too many gaps. CV side keeps one id per `Skill` (schema is single-id).

### W2 — Seed alias expansion and overrides (fixes F1, part of F3)
Add curated entries to `config/skill_aliases.yml` (ids `custom:<slug>` unless ESCO already
has a true equivalent — check `esco.skill_label` first to avoid one skill under two ids).
Process: (1) generate the candidate list from data — unmapped strings ranked by frequency
across CV + extracted jobs; (2) **you review the list** (approve/rename/merge); (3) commit
the yml. Include overrides for the F3 mismatches (decision D3). Tests: the existing
seed-file unit tests (no duplicate spellings), plus an integration check that the F1
examples resolve to one id each. Depends on W4a so existing wrong rows can be replaced.

### W3 — Verify tab covers label matches (fixes F3)
Generalise "embedding matches" to "auto matches" (methods `embedding` and `label`), with
the method and score shown, suspicious label matches first (matched skill's preferred label
differs from the string). Reject works for both methods (status `rejected`, protected from
re-mapping as today). Files: `core/skills/review.py` (`list_embedding_matches`,
`reject_embedding_match`), `apps/api/app/routers/skills.py`, `apps/ui/app/pages/6_Skill_Review.py`,
their tests. Also render `raw_example` as plain text, not markdown (link/image injection from
third-party JD text).

### W4 — Corrections must re-apply (fixes F4)
- **W4a** `map-skills --remap-all-auto`: delete every *auto-made* mapping (methods
  `label`, `embedding`, `none` + `open`), never human decisions (`resolved`, `rejected`,
  `dismissed`). Keep `--remap-unresolved` as is. Files: `core/skills/mapper.py`
  (`remap_unresolved`), `apps/pipeline/app/cli.py`. Tests: the existing remap test extended
  with label rows.
- **W4b** `map-cv-skills --refresh`: recompute every `canonical_id` (no UI can set one by
  hand today, so overwriting is safe) as a new labelled version. Files: `core/skills/cv_map.py`,
  CLI. Tests: a wrong id is replaced after an alias fix; a second run writes no version.
- **W4c** Lost-update protection (CV Editor and `map-cv-skills`): `write_truth_base` takes
  an `expected_version` and raises a conflict if the current version moved; the editor sends
  the version it loaded and asks the user to reload on conflict; `map_cv_skills` retries once.
  This touches Step 13 code (`core/cv/store.py`, `apps/api/app/routers/cv.py`,
  `5_CV_Editor.py`) — decision D4.

### W5 — Small hardening items
- `extract-job-skills` exits non-zero when failures occurred and nothing was extracted; print
  the failed count.
- `load-esco`: error on a header-only/empty skills file; report (don't drop silently) the
  concept ids that appear more than once (the 21 above) and what was kept.
- `map-skills` warns when `esco.skill_embedding` is empty or partial.
- Docs: `docs/esco.md` mentions `map-cv-skills` also refuses on a model mismatch; fix the
  "short form below" sentence and the seed-sync description (it also upserts `custom_skill`).

### W6 — Extraction scope and throughput (fixes F5)
Measure first, then pick (D5). Options, cheapest first: (1) scope by relevance — only jobs
whose `gold.dim_job.category` is in the categories you care about (`--category`), skip
snippet-only sources and the ~23 junk test rows (`--source greenhouse`); (2) run 2–4
workers with `OLLAMA_NUM_PARALLEL` set and measure the speed-up; (3) trim work per job
(e.g. extract from the requirements/responsibilities section only) — must re-check quality
with the golden set (score was 0.950); (4) hardware or a hosted model — **blocked by
DECISIONS §1** unless that decision is revisited. Also decide what to do with the ~23 junk
`test_source*` rows in the real DB (delete vs filter). Files: `core/skills/write_job_skills.py`, CLI.

### W7 — Operator runbook after the PRs merge (you)
1. `git pull` on `main`; restart the api and ui containers so they load the new
   router/page.
2. Run the migrations if any PR adds one (none planned).
3. `map-skills --remap-all-auto`, then `map-cv-skills --user-id <id> --refresh`, then
   `dbt run --select silver__skill silver__bridge_job_skill` and its tests.
4. Extract more jobs per W6 in the background; re-run step 3.
5. Review in the Skill Review page: Unmapped tab (resolve/custom/dismiss), then the auto-matches
   tab (confirm/reject every suspicious label match). Hand-check about 100 decisions;
   only then decide on `EMBEDDING_ACCEPT_COSINE` (currently 0.85, no evidence to move it).
6. Re-measure against the W0 baseline and record the numbers in the PR.

## 4. Order and PR plan

W0 → W1a → W4a → W2 → W3 → W4b → W5 → W6 → (W4c, W1b if approved). One PR per item or
per pair (W1a+W4a; W2 alone because you review its content; W3; W4b+W4c; W5+W6). Each PR
follows the project's flow: spec if design changed, TDD, DB-backed tests on a throwaway
database, then review.

Effort (rough): W0 S; W1a S–M; W4a S; W2 M (mostly your review time); W3 M; W4b S;
W4c M; W5 S; W6 M (measurement-heavy); W1b L.

## 5. Decisions (recorded 2026-09-20)

D3, D4 and D5 were answered explicitly. D1, D2, D6 and D7 were not individually
answered; they were accepted as recommended together with the rest.

| # | Decision | Recommendation | Outcome |
|---|---|---|---|
| D1 | Success targets (section 2) | Use them as a starting point; adjust after W0 | Recommendation followed |
| D2 | Keep `normalise_skill` frozen and add fallback forms (W1) | Yes — avoids re-keying 100k+ rows | Recommendation followed |
| D3 | For tools ESCO maps to a broad skill (NumPy, Scikit-Learn, Tableau, Kotlin, Visual Studio): specific `custom:` skills, or accept ESCO's generic skill? | Specific custom skills — gap analysis needs the tool, not the family | **Specific `custom:` skills** — confirmed for NumPy, scikit-learn, Kotlin and Visual Studio |
| D4 | Change Step 13 code for version-checked saves (W4c) now, or later? | Now — otherwise every editor save can wipe ids | **Now** (recommendation followed) |
| D5 | Extraction scope: categories/sources, parallelism, junk rows | Data/AI categories + Greenhouse only; measure 2–4 workers | **Recommendation followed**; the worker count is set by the W6 measurement |
| D6 | Compound-string expansion into several skills (W1b) | Defer until W1a results are known | Recommendation followed |
| D7 | Authentication for the review router | Not in this plan — Step 22a checklist item | Recommendation followed |

## 6. Out of scope

Step 15 scoring and Step 21 gap ranking; authentication; changing the local-only rule for
skill extraction; loading the other ESCO CSVs (ISCO groups, collections).
