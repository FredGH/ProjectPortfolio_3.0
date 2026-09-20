# ESCO skill normalisation (Step 14)

CV skills and job-description skills are mapped onto one vocabulary: the
[ESCO](https://esco.ec.europa.eu) skills taxonomy, plus a small set of
`custom:` skills for tools ESCO lacks. Design:
`docs/superpowers/specs/2026-09-19-step14-esco-skill-normalisation-design.md`.

ESCO is published by the European Commission. Check its current reuse terms
and attribution requirements on the ESCO portal before redistributing
anything derived from it. The release files are never committed here
(`data/esco/*` is gitignored).

## One-off setup

1. Download the ESCO **English CSV** release from the ESCO portal and unzip
   it. Copy `skills_en.csv`, `occupations_en.csv` and
   `occupationSkillRelations_en.csv` into `data/esco/`.
2. `alembic -c db/alembic.ini upgrade head` (migrations 0022, 0023).
3. Load, then embed (about 14k Ollama calls, resumable — re-run if interrupted):

   ```bash
   python -m apps.pipeline.app.cli load-esco data/esco
   python -m apps.pipeline.app.cli embed-esco
   ```

   In Docker: `docker compose run --rm pipeline load-esco /data/esco`.

   The `python -m apps.pipeline.app.cli ...` commands here and below assume
   `packages/core` is importable (it is inside the pipeline container). On a
   bare checkout run them as
   `PYTHONPATH=packages/core:apps/pipeline python -m app.cli ...`.

If a future release renames a CSV column, `load-esco` stops and names the
missing column rather than loading garbage.

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

## Changing the embedding model

`esco.skill_embedding` records the model per row and `map-skills` refuses to
run if it differs from `EMBEDDING_MODEL`. A different model (or dimension, which
needs a migration) means re-running `embed-esco`; no source data is lost.
