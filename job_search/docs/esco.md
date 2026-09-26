# ESCO skill normalisation (Step 14)

CV skills and job-description skills are mapped onto one vocabulary: the
[ESCO](https://esco.ec.europa.eu) skills taxonomy, plus a small set of
`custom:` skills for tools ESCO lacks. Design:
`docs/superpowers/specs/2026-09-19-step14-esco-skill-normalisation-design.md`.

ESCO is published by the European Commission. Check its current reuse terms
and attribution requirements on the ESCO portal before redistributing
anything derived from it. The release files are never committed here
(`data/esco/*` is gitignored).

## Prerequisites

- **The embedding model must be pulled into Ollama** before `embed-esco`,
  `map-skills` or `map-cv-skills`: `ollama pull nomic-embed-text` (whatever
  `EMBEDDING_MODEL` names).
- **Outside Docker**, `.env`'s hostnames are the compose service names. A
  command run from a bare checkout must override them to `localhost` —
  `DATABASE_URL`, `APP_DATABASE_URL` (both `@postgres` → `@localhost`) and
  `OLLAMA_BASE_URL` (`http://ollama:11434` → `http://localhost:11434`).
- `map-cv-skills` reads and writes the per-user CV truth base through the
  RLS-subject app role, so it needs **`APP_DATABASE_URL`** set as well as
  `DATABASE_URL`.

## How to run the commands below

Prefer the pipeline container — it already has `packages/core` importable and
the compose hostnames resolve:

```bash
docker compose run --rm pipeline <command> [args]
```

Fallback, from a bare checkout (with the `.env` overrides above):

```bash
PYTHONPATH=packages/core:apps/pipeline python -m app.cli <command> [args]
```

The command lines below use the module form
`python -m apps.pipeline.app.cli <command>`. With the container, drop that
prefix and run `docker compose run --rm pipeline <command>` with the same
arguments.

## One-off setup

1. Download the ESCO **English CSV** release and unzip it. Copy
   `skills_en.csv`, `occupations_en.csv` and `occupationSkillRelations_en.csv`
   into `data/esco/` (mounted at `/data/esco` in the pipeline container).

   - **Download page:** <https://esco.ec.europa.eu/en/use-esco/download>
   - **Version:** ESCO **v1.2.1** was the current release on that page as of
     2026-09-20 (page dated 10/12/2025). Pick the latest version listed, and
     note which one you loaded — a newer release may rename columns or files.
   - **On the page:** choose the version, the content (occupations and
     skills & competences — the loader also needs the occupation–skill
     relations), file type **CSV**, and language **English**. You then accept
     the privacy statement and enter your email address; the download link
     is emailed to you (it is not a direct link, so it cannot be scripted).
   - The portal does not list the CSV file names in the zip. The names above
     are what `load-esco` expects; if yours differ, `load-esco` stops and
     names the missing file or column.
2. `alembic -c db/alembic.ini upgrade head` (migrations 0022, 0023; 0024 grants
   the API role `DELETE` on `silver.skill_alias`, which *Reopen* needs).
3. Load, then embed (about 14k Ollama calls, resumable — re-run if interrupted):

   ```bash
   docker compose run --rm pipeline load-esco /data/esco
   docker compose run --rm pipeline embed-esco
   ```

If a future release renames a CSV column, `load-esco` stops and names the
missing column rather than loading garbage.

**After the first real load, audit ambiguous labels.** Several ESCO skills can
share one `label_norm` (e.g. a qualifier that normalisation strips). The label
stage resolves such a clash silently — preferred label first, then the smallest
skill id — so check what it is deciding for you:

```sql
SELECT label_norm, count(*) AS skills, string_agg(skill_id, ', ') AS skill_ids
FROM esco.skill_label GROUP BY label_norm HAVING count(*) > 1 ORDER BY 2 DESC;
```

Anything in that list that matters is worth a `config/skill_aliases.yml` entry
pinning the string to the skill you actually mean.

**Limitations.** A same-model `embed-esco` run only embeds skills that have no
embedding yet or whose stored `embedding_model` differs from the current one.
So if a later `load-esco` renames a skill's preferred label, its old vector is
**not** refreshed. `esco.skill_embedding` is a derived, rebuildable cache
(see migration 0022); to rebuild, run
`DELETE FROM esco.skill_embedding [WHERE skill_id = ...]` and then re-run
`embed-esco`.

## Routine run (after new jobs land and are deduplicated)

```bash
python -m apps.pipeline.app.cli extract-job-skills   # local LLM; slow on CPU
python -m apps.pipeline.app.cli map-skills           # seed aliases, then map new strings
(cd dbt && dbt run --select silver__skill silver__bridge_job_skill)
python -m apps.pipeline.app.cli map-cv-skills --user-id <uuid>   # after a CV upload/edit
```

If you extract from the **Skill Extraction Runner** page instead of the CLI, a
run that completes does the `map-skills` step for you (and shows the result); only
the `dbt run` line stays manual, because dbt can't run inside the API image. See
README.md's "Running a batch from the UI".

`map-cv-skills` writes a new CV truth-base version labelled "ESCO skill
normalisation" (only if something changed), so it is traceable and reversible
in the CV Editor's history. By default it only fills skills that have no id yet.

To bring a CV in line with corrected decisions, add `--refresh`:

```bash
python -m apps.pipeline.app.cli map-cv-skills --user-id <uuid> --refresh
```

It recomputes **every** skill's id from the current mapping: an id that no longer
matches is replaced, and an id whose string no longer resolves to a skill (a
dismissed or reopened decision) is cleared. It writes one version labelled
"ESCO skill normalisation (refresh)" and nothing at all if no id changed, so a
second run is a no-op. Overwriting is safe because no UI sets an id by hand — the
CV Editor only carries ids across a save. A skill added in the CV Editor while
the command is running is left alone. The output reports `changed=N`.

## Strings with a qualifier

A skill written with a parenthetical qualifier — `MySQL (RDS)`,
`AWS (S3, ECS/Fargate, Lambda)`, `CI/CD (Jira+Git+Terraform)` — is looked up
under two keys, whole string first: the whole normalised string, then the same
string with the qualifier removed (`mysql`, `aws`, `ci/cd`). Alias and ESCO-label
lookups use both; a hit on the whole string always beats a hit on the head. The
embedding stage still sees the whole string only. The row in `skill_mapping` is
keyed by the whole string, as before (`normalise_skill` is unchanged).

Not done: splitting a list such as `TypeScript/React` or `Hive, Impala` into
several skills. That needs a schema change and is a separate decision.

This only applies to strings mapped from now on. To re-apply it to strings that
are already mapped, run `map-skills --remap-all-auto` (below). A string a human
already resolved (for example a compound string someone made its own custom
skill) is never re-mapped: correct it by hand with the `UPDATE`s under *Seed
aliases*.

## Evaluating skill extraction

- `run-evals --task skill_extraction --provider target` runs the 20-case golden
  set through the extraction prompt. Use `--provider target`, **not**
  `--provider local`: the `local` label only applies to tasks that have separate
  `local_*` config, and returns `provider_not_configured` for this local-only
  task. It needs the database and a local Ollama with `llama3.1:8b`.

## Exit codes and warnings

- **`extract-job-skills`** exits **1** when jobs failed and none succeeded (for
  example the LLM provider is down), and says how many. If at least one job
  succeeded it exits 0 and prints `failed_jobs=N`; those jobs are retried on the
  next run.
- **A looping model is retried once, then cut off.** Each chunk's reply is
  capped at `MAX_OUTPUT_TOKENS` (2,048; a real skill list is well under
  1,000). At `temperature=0`, llama3.1:8b can settle into repeating a block of
  output forever once it exceeds Ollama's default `repeat_last_n` (64 tokens)
  — one real job repeated the same ~20 skills verbatim until it hit the cap.
  A reply that hits the cap is retried **once**, that single chunk only, with
  `repeat_penalty=1.15` and `repeat_last_n=256`
  (`core.skills.jd_extract.REPEAT_PENALTY` / `REPEAT_LAST_N`) — wide enough
  to break the loop. The penalty is **never** applied to a chunk whose first
  reply was fine: sending it on every call was tried and measurably hurt
  quality on chunks that were already fine (fewer skills found, and
  sometimes invalid JSON from inline comments the penalty induced) — see
  `README.md` for how this was found and the settings that were tried. A
  reply still truncated after the retry is discarded, the job counts toward
  `failed_jobs`, and the next run retries it; since every call is
  deterministic, a chunk that still loops after the penalty will loop
  identically on every future run too.
- **`map-skills`** prints `map-skills: warning: …` when `esco.skill_embedding` is
  empty, or covers only some skills, for the configured model. The similarity
  stage would otherwise silently match nothing, or miss matches. The alias and
  label stages still run; fix it with `embed-esco`.
- **`load-esco`** refuses a release file that has a header but no data rows
  (loading it would replace nothing and look like a success), and reports skill
  concept ids repeated in `skills_en.csv`. ESCO v1.2.1 repeats 21 ids on
  identical rows (13,960 rows, 13,939 distinct skills). The last row supplies the
  skill record and the labels of every row are merged; the command prints how
  many ids repeat, how many of those differ in content, and up to five of them.
  The `skills=` figure counts distinct skills, not CSV rows.

## Scoping extraction (the slow step)

The Skill Extraction Runner UI page (`http://localhost:8501/Skill_Extraction_Runner`)
is the recommended way to run a scoped batch interactively; the
`--source`/`--category`/`--country` CLI flags documented below remain for
scripted/CI use. See README.md's "Running a batch from the UI instead of the
shell script" for how it works.

`extract-job-skills` is the expensive command: a local 8B model on CPU took about
150 s per Greenhouse job (roughly two LLM calls each) on the first real run, so
"every pending job" was about 90 hours. Scope it instead:

```bash
python -m apps.pipeline.app.cli extract-job-skills --source greenhouse \
    --category data_engineer --category analytics_engineer \
    --category data_scientist --category ai_ml_engineer
```

- `--source NAME` (repeatable) keeps only jobs whose winning source is `NAME`.
  Name the sources you want and everything else is skipped: the snippet-only
  sources (`adzuna`, `jooble`, `reed`: 300–500 characters, too short to yield many
  skills) and any leaked `test_source*` rows in a dev database.
- `--category NAME` (repeatable) keeps only jobs whose `gold.dim_job.category` is
  one of the names. A job that has not been categorised yet never matches, so run
  `classify-jobs` and the gold dbt models first. See the categories and their
  counts with `SELECT category, count(*) FROM gold.dim_job GROUP BY 1`.
- `--country ISO` (repeatable, e.g. `--country GB`) keeps only jobs whose
  `gold.dim_job.country_iso` is one of the codes. Resolved by
  `core.normalisation.location.normalise_location` at blocking-key time
  (`compute-blocking-keys`), not by dbt — a location that never resolves
  (most free-text locations don't) leaves `country_iso` `NULL` and never
  matches. **Check real coverage before relying on it**:
  `SELECT country_iso, count(*) FROM gold.dim_job GROUP BY 1 ORDER BY 2 DESC`
  — a country_iso fix only reaches `gold.dim_job` after re-running
  `compute-blocking-keys` and rebuilding the gold models that join it
  (`dim_job`, `dim_company`, `fct_market_demand`); it does **not** need
  `cluster-jobs`/`compute-survivorship` to re-run, since `job_group_id` and
  survivorship winners never depend on country (PLAN.md Step 10's stability
  guarantee holds).
- `--limit N` caps the run, which is a good way to time a trial before a long one.
- Filters combine (source AND category AND country; several values for the
  same flag are OR-ed). The command prints how many pending jobs match before
  it starts, so a typo shows up as `0 pending job(s) match` immediately.

As measured on 2026-09-21, the pending Greenhouse jobs were 2,117, of which the four
data and AI categories above are 257. Extraction is resumable: each job commits on
its own, and a re-run only picks up jobs without an extraction.

## Reviewing what did not map

Open the **Skill Review** page. It opens with a collapsed **User Guide**
accordion that explains every action and its consequences. Three tabs:

- **Unmapped** — strings that matched nothing. Accept the suggestion, search
  ESCO for the right skill, mark it as a custom skill, or dismiss it.
- **Auto-matches — verify** — strings the system matched on its own, by an
  exact ESCO label or by similarity. Confirm or reject each; a wrong match is
  otherwise invisible. Matches that look suspicious are listed first.
- **Decisions — reopen** — every string you resolved or dismissed. *Reopen*
  withdraws a decision so it can be made again (see *Correcting a decision*).

A resolution becomes an alias, so it applies to every future CV and JD
string that normalises the same way. Aliases are shared across users.

### What to click

Each card is one question: what should this string mean in the shared skill
vocabulary? The list is sorted by how many jobs mention the string, so work
from the top.

**Unmapped tab**

| Button | Use it when | What happens |
|---|---|---|
| **Accept suggestion: X (0.81)** | The closest ESCO skill is right. It is the best candidate that fell below the auto-accept threshold (0.85), so the system asks instead of guessing. Not offered for a string you already rejected. | The string maps to X and is saved as an alias. |
| **Map to selected** | The suggestion is wrong but another ESCO (or existing custom) skill fits: type in *Search ESCO / custom skills*, pick a result. | The string maps to the chosen skill and is saved as an alias. |
| **Mark as custom skill** | It is a real skill ESCO does not have (Terraform, dbt, Snowflake …). The box is pre-filled with the original text; edit it to the name you want. | A `custom:<slug>` skill is created (or reused) and the string maps to it. |
| **Dismiss** | It is not a skill, or you do not care about it ("Strong work ethic"). | It leaves the queue for good and stays unmapped. Only unmapped strings can be dismissed. |

**Auto-matches — verify tab** — each row shows the string, the skill it was
mapped to, how (an exact ESCO label, or the similarity), how many jobs it
appears in, and whether it is on your CV. A yellow warning means the string is
not the skill's own name — ESCO files many tools as hidden labels under a broad
skill (`kotlin` under "computer programming", `numpy` under "software
components libraries") — so check those first. Order: warned label matches,
then similarity matches (least confident first), then the label matches that
name their skill (`sql` -> "SQL"); most-used first among the label matches.
Curated seed aliases are not listed.

| Button | What happens |
|---|---|
| **Confirm** | Keeps the match and saves it as a permanent alias. |
| **Reject** | Undoes the match; the string goes back to the Unmapped tab and is protected from being auto-mapped again. Pick the right skill there (search, or create a custom skill). |

Example: `distributed systems` shows *Accept suggestion: distributed computing
(0.81)*. That is a reasonable match, so Accept. If you would rather keep the
two apart, create a custom skill instead.

### When a decision takes effect

A decision changes `silver.skill_mapping` and the alias table immediately, but
jobs and CVs already processed are not touched until you re-run the derived
steps:

```bash
(cd dbt && dbt run --select silver__skill silver__bridge_job_skill)   # jobs
python -m apps.pipeline.app.cli map-cv-skills --user-id <uuid> --refresh   # a CV
```

### Correcting a decision

A seed-file entry cannot fix a wrong decision: a review alias is protected from
the seed sync, and a row a human resolved is never re-mapped
(`--remap-all-auto` skips it). Use the **Decisions — reopen** tab instead. Find
the string (search matches the string or its skill), then click **Reopen**:

| The string was | Reopen does |
|---|---|
| **Resolved** | Deletes the string's review alias, so the old target stops applying to future strings, and returns it to the Unmapped tab as *Previously rejected: <old target>*. Being `rejected`, it is skipped by `--remap-unresolved` and `--remap-all-auto`, and *Accept suggestion* is not offered for the old target. Then resolve it correctly (or dismiss it). A custom skill the old decision created is kept. |
| **Dismissed** | Returns it to the Unmapped tab as an ordinary open string. |

Not reopenable: a string mapped by a curated seed alias (edit
`config/skill_aliases.yml`), an automatic match (use *Reject*), a string that is
already unmapped. Reopen changes `silver.skill_mapping` and the alias table
only; the CV and job-bridge steps above are still needed, and other strings
that were auto-mapped through the withdrawn alias keep their id until
`map-skills --remap-all-auto`.

**Correcting a wrong label match.** On the verify tab, Reject it, then pick the
right skill on the Unmapped tab (*Map to selected*, or *Mark as custom skill*
for a tool ESCO files under a broad skill, like `numpy`). To fix many at once,
add aliases to `config/skill_aliases.yml` and run
`map-skills --remap-all-auto`. Either way the jobs side follows after a dbt
rebuild, and a CV skill that already holds the wrong id needs
`map-cv-skills --refresh` (see the next note).

**No authentication yet.** These endpoints (`/skills/review*`, `/skills/search`)
are unauthenticated writes to shared-zone taxonomy — anyone who can reach the
API can re-point a skill for every user, and (since migration 0024) delete a
review alias through `/skills/review/reopen`. Auth lands in **Step 22a**; this
router must be on that step's checklist.

**A reject or reopen does not correct a CV that already has the id.** `reject`,
`reopen` and `map-skills --remap-unresolved` / `--remap-all-auto` change
`silver.skill_mapping` only. A CV whose `skills[].canonical_id` was already filled
with the now-rejected id keeps it, because a plain `map-cv-skills` never
overwrites an existing `canonical_id`. Run
`map-cv-skills --user-id <uuid> --refresh` to replace or clear it. (Before
`--refresh` existed the workaround was to rename the skill in the CV Editor,
save, rename it back and save again, then re-run `map-cv-skills`.)

## Tuning the similarity threshold

`core.skills.mapper.EMBEDDING_ACCEPT_COSINE` (0.85) is a starting value, not
an empirically tuned one. After the first real run, hand-check about 100
entries on the verify tab; if too many are wrong raise it, if the Unmapped
tab is swamped with obvious matches lower it, then run
`map-skills --remap-unresolved`. Human decisions (confirmed, rejected,
resolved, dismissed) are never re-mapped.

## Seed aliases

`config/skill_aliases.yml` is the committed starting set: GCP, AWS, Kubernetes,
PostgreSQL, and tools ESCO lacks or files under a broad skill (PyTorch,
TensorFlow, Power BI, Tableau, Jira, Linux, React), plus Git pinned to ESCO's own
skill. Once real ESCO data is loaded, check whether ESCO already has an
equivalent skill for each `custom:` entry and, if so, re-point the entry at the
ESCO skill id so the same skill does not exist under two ids.

**Re-pointing a seed entry does not migrate by itself.** The sync only writes
`silver.skill_alias` rows (and upserts `silver.custom_skill` for `custom:`
entries), and the alias is consulted when a string is *first*
mapped, so everything already mapped keeps the old id: `silver.skill_mapping`
rows, therefore the dbt bridge, and any CV `canonical_id` written from them.
Re-apply it with:

```bash
# Syncs the seed file, clears every auto-made mapping, re-maps them.
python -m apps.pipeline.app.cli map-skills --remap-all-auto
(cd dbt && dbt run --select silver__skill silver__bridge_job_skill)
```

`--remap-all-auto` deletes alias-, label-, embedding- and open rows (anything
nobody decided) and never touches `resolved`, `rejected` or `dismissed` rows.
A string a human resolved to the old id keeps it, and its review alias is
protected from the seed sync; migrate those by hand, in this order:

**The `llm` method.** `llm-map-skills` asks Claude to pre-review strings that are
still unmapped and records a high-confidence match with `method = 'llm'`. Such a
row is shown in *Auto-matches — verify* as "matched by Claude" and is only made
an alias when a person confirms it. `--remap-all-auto` keeps `llm` rows (it does
not delete them), so a re-map never discards the model's work or spends API
calls again.

```sql
-- 1. The alias rows themselves.
UPDATE silver.skill_alias SET skill_id = '<new-esco-id>'
WHERE skill_id = 'custom:x';

-- 2. Every string already mapped to the old id.
UPDATE silver.skill_mapping SET skill_id = '<new-esco-id>'
WHERE skill_id = 'custom:x';
```

then rebuild the dbt models as above.

CV `canonical_id`s are **not** covered by any of that: a plain `map-cv-skills`
never overwrites an id that is already set, so a CV holding `custom:x` keeps it
until you run `map-cv-skills --refresh` (see the reject note above). Deleting the
now-unused
`silver.custom_skill` row is optional, and only safe once nothing references it.

**Aliases removed from the seed file are never deleted.** The loader upserts;
it does not diff. Dropping an entry from `config/skill_aliases.yml` leaves its
`silver.skill_alias` row in place and still in effect — delete it explicitly
(`DELETE FROM silver.skill_alias WHERE alias_norm = '<norm>' AND source = 'seed'`).

## Changing the embedding model

`esco.skill_embedding` records the model per row; `map-skills` **and**
`map-cv-skills` refuse to run if it differs from `EMBEDDING_MODEL`. A different
model (or dimension, which needs a migration) means re-running `embed-esco`; no
source data is lost.
