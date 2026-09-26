# LLM skill pre-review — design

Status: design approved in conversation 2026-09-26; this spec awaits written review.

## Purpose

`silver.skill_mapping` holds about 5,000 strings that neither an alias, an ESCO
label nor the 0.85 embedding threshold could map (`review_status = 'open'`).
The top 500 each appear in only 2–6 jobs: a long tail, too big to review by
hand and not reducible by reviewing only the most-requested strings.

Goal: a model pre-reviews them so a person only handles the cases it is unsure
about, and spot-checks the rest.

Success criteria:

1. On strings a person has already resolved, the model's high-confidence
   picks agree with the person at a rate we measure and report **before** any
   real-backlog string is changed.
2. After a backlog run, the Unmapped tab holds only strings the model marked
   `no_equivalent` or `unsure`, each carrying the model's note.
3. Nothing the model applies becomes a permanent alias without a human click.
4. No human decision (`resolved`, `rejected`, `dismissed`) is ever changed.

Out of scope: choosing custom skills for the user; changing the 0.85
threshold; an in-UI "run" button; local-Ollama support (Claude API only — the
user chose it; the task routing in `config/llm_tasks.yml` keeps it swappable).

## Behaviour

For each string with `review_status = 'open'` and `llm_checked_at IS NULL`:

1. Take the string and its top 5 ESCO nearest neighbours (id, preferred label,
   cosine), from the same `esco.skill_embedding` query the mapper uses.
2. Send ~20 strings per call to Claude (task `skill_mapping`, model
   `claude-haiku-4-5-20251001`, prompt family `claude`) and parse a JSON verdict
   per string:
   - `match` — one of the 5 candidate ids, plus `confidence`: `high` | `low`.
   - `no_equivalent` — with an optional short `custom_label` suggestion.
   - `unsure`.
3. Apply the verdict:
   - `match` + `high`: `method='llm'`, `skill_id` = the pick, `score` = its
     cosine, `review_status = NULL`. It joins the "Auto-matches — verify" list.
   - anything else: the row stays `open`; the verdict is stored for display.
   In every case set `llm_checked_at = now()` so the string is not asked again.

The model may only choose from the 5 candidates; an id outside them is treated
as `unsure`. Malformed JSON, a missing string in the reply, or an API error
leaves those strings unchecked (`llm_checked_at` stays NULL) so a later run
retries them.

## Data model (migration 0027)

`silver.skill_mapping`:

- `method` CHECK gains `'llm'`. The invariant `(skill_id IS NULL) = (method =
  'none')` still holds.
- `llm_verdict text NULL` — `match_low` | `no_equivalent` | `unsure` |
  `match_high` (for display and audit).
- `llm_custom_label text NULL` — the model's suggested custom-skill name.
- `llm_note text NULL` — one-line reason, shown on the card.
- `llm_checked_at timestamptz NULL`.

Grants: `job_search_app` already has UPDATE on `skill_mapping`; the CLI uses the
owner role, the post-run hook the app role, so both work.

## Interaction with existing code

- `core/skills/review.py`: add `'llm'` to `_AUTO_METHODS`, so the verify list
  includes it and `resolve_to_skill` (Confirm → writes `source='review'` alias,
  status `resolved`) and `reject_auto_match` accept it. `list_auto_matches`
  filters `method IN ('embedding','label','llm')` and shows the note.
- `list_unmapped` returns the note and `llm_custom_label`; the card shows
  "Claude: no ESCO equivalent — suggests custom skill 'X'" with a one-click
  "Create custom skill" reusing `resolve_to_custom`.
- `mapper.remap_unresolved` deletes only `open` rows, so `llm` rows survive.
  A rejected `llm` match goes to `rejected` with `llm_checked_at` set, so it is
  not re-proposed.
- The dbt bridge reads `skill_mapping.skill_id`, so an unconfirmed `llm` match
  counts in the bridge exactly as an unconfirmed `embedding` match does today.
  This is the same trade-off, called out here so it is a decision, not a
  surprise.

## Components

- `core/skills/llm_map.py` — `propose_matches(engine, *, adapters, limit=None,
  batch_size=20, raw_norms=None) -> LlmMapSummary` (checked, applied, left_open,
  failed, tokens/cost estimate). Pure of I/O policy: adapters are injected, as
  the gateway requires.
- `prompts/skill_mapping/claude.v1.md` — the prompt; `config/llm_tasks.yml`
  entry `skill_mapping` (anthropic, haiku 4.5, family `claude`).
- `apps/pipeline` CLI: `llm-map-skills [--limit N] [--dry-run]`; prints counts
  and estimated cost.
- Post-run hook (`post_run_mapping.py`): after `map_pending`, calls
  `propose_matches(limit=300)`; best-effort — any failure is appended to the
  run's `mapping_summary` and never fails the run. If no `ANTHROPIC_API_KEY`,
  the step is skipped with a note.
- UI: verify tab and Unmapped cards show the note (Skill Review page).

## Validation before trust

`llm-map-skills --evaluate`: run the model over strings already `resolved` by a
person (their alias target is ground truth), **without writing anything**, and
print: how many it matched high/low/none/unsure, and for high-confidence picks
the agreement with the human choice. If high-confidence agreement is below
90%, stop and revisit the prompt or make `high` require a stricter rule before
running on the backlog. The result goes in the PR description.

## Testing

Real Postgres, no DB mocking; only the Anthropic call is faked, by an injected
adapter.

- verdict application: high match → `llm` row; low/none/unsure → stays open with
  verdict stored; out-of-candidate id → `unsure`.
- retry safety: malformed JSON / missing string / adapter error leaves rows
  unchecked; a second run picks them up; a checked string is not re-asked.
- protection: `resolved`, `rejected`, `dismissed` rows untouched.
- review flow: Confirm on an `llm` row creates the `review` alias; Reject moves
  it to `rejected` and it is not re-proposed.
- post-run hook: failure recorded in `mapping_summary`, run stays `completed`;
  missing key skips cleanly.
- CLI `--evaluate` writes nothing.

## Cost and privacy

About 5,000 short strings at ~20 per call is ~250 calls with 5 candidates each:
roughly 0.4M input tokens, i.e. well under a few dollars with Haiku (the CLI prints the real estimate before spending). Only skill strings and ESCO labels are sent —
no job text, CV text or personal data. The CLI prints an estimate before
spending, and `--limit` caps a run.
