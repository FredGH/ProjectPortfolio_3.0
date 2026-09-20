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

The command lines below are written in the short
`python -m apps.pipeline.app.cli ...` form; run them either way.

## One-off setup

1. Download the ESCO **English CSV** release from the ESCO portal and unzip
   it. Copy `skills_en.csv`, `occupations_en.csv` and
   `occupationSkillRelations_en.csv` into `data/esco/` (mounted at `/data/esco`
   in the pipeline container).
2. `alembic -c db/alembic.ini upgrade head` (migrations 0022, 0023).
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

`map-cv-skills` writes a new CV truth-base version labelled "ESCO skill
normalisation" (only if something changed), so it is traceable and reversible
in the CV Editor's history.

## Evaluating skill extraction

- `run-evals --task skill_extraction --provider target` runs the 20-case golden
  set through the extraction prompt. Use `--provider target`, **not**
  `--provider local`: the `local` label only applies to tasks that have separate
  `local_*` config, and returns `provider_not_configured` for this local-only
  task. It needs the database and a local Ollama with `llama3.1:8b`.

## Reviewing what did not map

Open the **Skill Review** page. Two tabs:

- **Unmapped** — strings that matched nothing. Accept the suggestion, search
  ESCO for the right skill, mark it as a custom skill, or dismiss it.
- **Embedding matches — verify** — strings matched by similarity. Confirm or
  reject each; a wrong match is otherwise invisible.

A resolution becomes an alias, so it applies to every future CV and JD
string that normalises the same way. Aliases are shared across users.

**No authentication yet.** These endpoints (`/skills/review*`, `/skills/search`)
are unauthenticated writes to shared-zone taxonomy — anyone who can reach the
API can re-point a skill for every user. Auth lands in **Step 22a**; this
router must be on that step's checklist.

**A reject does not correct a CV that already has the id.** `reject` and
`map-skills --remap-unresolved` change `silver.skill_mapping` only. A CV whose
`skills[].canonical_id` was already filled with the now-rejected id keeps it,
because `map-cv-skills` never overwrites an existing `canonical_id`. To correct
one: clear that skill's id — in the CV Editor, rename the skill and save, then
rename it back and save again (a save keeps the id only while the name is
unchanged) — then re-run `map-cv-skills --user-id <uuid>`.

## Tuning the similarity threshold

`core.skills.mapper.EMBEDDING_ACCEPT_COSINE` (0.85) is a starting value, not
an empirically tuned one. After the first real run, hand-check about 100
entries on the verify tab; if too many are wrong raise it, if the Unmapped
tab is swamped with obvious matches lower it, then run
`map-skills --remap-unresolved`. Human decisions (confirmed, rejected,
resolved, dismissed) are never re-mapped.

## Seed aliases

`config/skill_aliases.yml` is the committed starting set (GCP, AWS, Kubernetes,
PostgreSQL). Once real ESCO data is loaded, check whether ESCO already has an
equivalent skill for each `custom:` entry and, if so, re-point the entry at the
ESCO skill id so the same skill does not exist under two ids.

**Re-pointing a seed entry is not a migration.** The sync only updates
`silver.skill_alias`, and the alias is consulted when a string is *first*
mapped. Everything already mapped keeps the old id: `silver.skill_mapping`
rows, therefore the dbt bridge, and any CV `canonical_id` written from them.
Migrate by hand, in this order:

```sql
-- 1. The alias rows themselves (only if the sync has not already moved them).
UPDATE silver.skill_alias SET skill_id = '<new-esco-id>'
WHERE skill_id = 'custom:x';

-- 2. Every string already mapped to the old id.
UPDATE silver.skill_mapping SET skill_id = '<new-esco-id>'
WHERE skill_id = 'custom:x';
```

```bash
# 3. Rebuild the derived models.
(cd dbt && dbt run --select silver__skill silver__bridge_job_skill)
```

CV `canonical_id`s are **not** covered by any of that: `map-cv-skills` never
overwrites an id that is already set, so a CV holding `custom:x` keeps it until
that skill is re-saved in the CV Editor with its id cleared (see the reject note
above) and `map-cv-skills` is re-run. Deleting the now-unused
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
