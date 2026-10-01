# Step 17 — Tailored CV generation with the fabrication guard: design

Status: draft for review · 2026-10-01 · PLAN.md Step 17 (8 pts)

## Goal

Given one user and one real job (`job_group_id`), produce a CV tailored to
that job that **cannot contain anything the user can't defend**: every
generated line traces to the user's CV truth base, and anything that
doesn't is put in front of the user for an explicit decision.

Two renderings of the result are wanted (follow-on sub-projects, see
*Decomposition*): a plain ATS `.docx` for job portals and a designed PDF
matching the user's own template. Step 17 delivers the **content** both
will render, not the rendering.

## Decisions already made

| Question | Decision |
|---|---|
| Scope of this pass | Backend **and** the review UI |
| How a run starts | On demand, picked from the user's top-scored jobs (API + CLI + UI) |
| What "accept orphan" means | The user links it to an **existing truth-base bullet**; the truth base is never modified; "true but no source" is not allowed |
| Generation approach | **Assemble, don't generate** — code builds the document from the truth base; the model only returns per-bullet instructions |
| Critic provider | Claude, always (`fabrication_critic` is already routed to `anthropic`) |
| Tailor provider | New task `cv_tailoring`: local (Ollama) in dev, Claude variant ready, per DECISIONS.md §1 |
| Outputs | ATS docx + designed PDF, as follow-on sub-projects |

## Decomposition

1. **17 — tailoring (this spec).** Tailor, checks, critic loop, persistence,
   API/CLI, review UI, adversarial test. Output: an approved
   `TailoredDocument` (below).
2. **18a — ATS docx** (PLAN.md Step 18). Locked single-column template, no
   tables / text boxes / images / headers, standard headings,
   `MM/YYYY – MM/YYYY` dates, acronym expansion, title injected as a
   template field and asserted against the *rendered* text, filename
   `<surname>_<title_for_display>_<company>.docx`, `.txt` twin diffed against
   the docx. Its own spec and plan.
3. **18b — designed PDF.** Matches the user's `.pages` template. **Blocked on
   the user exporting the template** (`.docx` and `.pdf`) so its structure
   can be inspected. Its own spec and plan, written once the export exists.

Both renderers consume the same `TailoredDocument`, so Step 17 does not
change whichever rendering approach 18b ends up needing.

## Architecture

```
top-scored jobs ──► [Tailor LLM] ──► per-bullet instructions
 (scoring.job_score)   cv_tailoring        │
                                           ▼
 truth base ─────────────────────► [assemble.py]  deterministic
 (read_truth_base)                         │
                                           ▼
                                   TailoredDocument (draft)
                                           │
                       ┌───────────────────┴──────────────────┐
                       ▼                                      ▼
                [checks.py] code-only                 [critic.py] Claude
        evidence ids · titles unchanged ·     does each reworded bullet's claim
        headline exact · keyword coverage     follow from its cited source text?
                       └───────────────────┬──────────────────┘
                                           ▼
                        failures? ── yes, attempts < 2 ──► back to Tailor
                                           │ no / out of attempts
                                           ▼
                     persist: tailored_cv + orphan_bullet rows
                                           ▼
                          review UI: link / reject, see stretch
```

All new code lives in `packages/core/core/tailoring/`. Everything is keyed
on `job_group_id`, never a source posting.

## The document model

`TailoredDocument` (pydantic, `core/tailoring/schema.py`) is the contract
between Step 17 and both renderers.

- `target_title` — `gold.dim_job.title_for_display`, **injected**, never
  generated.
- `headline` — always equal to `target_title`.
- `summary` — `{text, evidence_refs}` or none.
- `experience` — one entry per truth-base experience, in truth-base order:
  `company`, `title`, `start`, `end` copied from the truth base by index
  (**never passed through the model**), plus `bullets`, each
  `{text, evidence_refs: [bullet_id], origin}` where origin is
  `original` (text identical to its single source bullet), `reworded`
  (valid refs, changed text) or `orphan` (no valid ref).
- `skills` — ordered skill names drawn from the truth-base skills.
- Static sections (`education`, `qualifications`, `projects`,
  `publications`, `activities`, contact fields) copied verbatim.
- `stretch` — `{is_stretch, reason}` when the target title implies
  seniority or scope the truth base doesn't evidence.
- `keyword_coverage` — `{covered, missing_evidenced, missing_unevidenced}`.

## The Tailor

New LLM task `cv_tailoring` in `config/llm_tasks.yml` (local Ollama in dev,
with `local_*` and Claude variants as other tasks do), prompts under
`prompts/cv_tailoring/` (`local.v1.md`, `claude.v1.md`).

Input: the truth base (with `bullet_id`s), the job's `title_for_display`,
`description` and the job's required skills (`silver__bridge_job_skill`).
Output JSON, parsed with the existing `json_response` helper:

```json
{
  "summary": {"text": "...", "evidence_refs": ["b_ab12"]},
  "experience": [
    {"truth_index": 0,
     "bullets": [{"text": "...", "evidence_refs": ["b_cd34"]}]}
  ],
  "skills": ["dbt", "Airflow"]
}
```

The model may drop, reorder and reword bullets and may surface skills the
user has. It cannot change a company, title or date, because those are not
in its output. It cannot add content without it showing up as an `orphan`
(no valid ref) or being judged by the critic (valid ref, changed text).

## Checks (code only, `checks.py`)

1. Every `evidence_ref` exists in the truth base.
2. Every experience entry's company, title, start and end equal the truth
   base's (asserted on the assembled document, even though assembly makes it
   true by construction — this is the plan's "the second assertion is the one
   that matters", kept as defence in depth).
3. `headline` equals `title_for_display` exactly.
4. Keyword coverage: job skills present in the tailored text, split into
   *missing but evidenced in the truth base* and *missing and not evidenced*.
   Only the first group is ever fed back to the Tailor ("surface these");
   the second is reported and never requested, so the loop cannot push the
   model toward claiming skills the user lacks.
5. Content-level ATS hygiene is enforced **mechanically at assembly**
   (`assemble.clean_text`: leading bullet glyphs, decorative symbols and
   emoji are stripped from generated text), not checked after the fact —
   there is nothing to retry because the fix is deterministic. Layout rules
   belong to the template in 18a.

## The critic (`critic.py`)

Runs through the `fabrication_critic` task. A guard against drift: the
module asserts the resolved provider is `anthropic` and raises otherwise, and
a test asserts the same against `config/llm_tasks.yml`.

For every `reworded` bullet and the summary, the critic receives the
generated text and the **source bullet text(s)** it cites and returns
`supported` true/false plus the unsupported claim. "Unsupported" means any
added metric, technology, scale, team size, ownership or outcome not present
in the sources. It also judges the title-seniority **stretch** by comparing
the target title with the truth-base history; this is advisory, never a
failure. (`scoring.job_score.llm_stretch_flag` from Step 15 exists as a
hint but is not relied on: it answers a different question.)

## The loop (`loop.py`)

Tailor → assemble → checks → critic. If anything failed and fewer than two
retries have been used, call the Tailor again with the concrete failures
appended (which bullets were unsupported and why, which evidenced keywords to
surface). After the second retry the loop stops and persists whatever
remains, **it never emits an unchecked bullet as approved**.

Remaining problems become `orphan_bullet` rows:

- `kind = orphan` — no valid evidence ref.
- `kind = unsupported` — the critic rejected a reworded bullet.

## Orphan decisions

Per answered question: **accept = link to an existing truth-base bullet.**
The user picks the bullet that evidences it; that bullet's id becomes the
`evidence_ref`; nothing is written to the truth base. **Reject** drops the
bullet, except that an `unsupported` bullet citing exactly one valid source
reverts to that source bullet's original text. A tailored CV is
`approved` only when no orphan is `pending`.

## Persistence (migration 0032, schema `tailoring`)

`tailoring.tailored_cv`: `id`, `user_id` (RLS, same policy pattern as
`scoring.job_score`), `job_group_id`, `truth_base_version`, `target_title`,
`content` jsonb (the `TailoredDocument`), `status`
(`generating` | `needs_review` | `approved` | `failed`), `attempts`,
`stretch` jsonb, `tailor_model`, `tailor_prompt_version`, `critic_model`,
`critic_prompt_version`, `created_at`, `updated_at`. Every run is a new row;
the UI shows the latest per job.

`tailoring.orphan_bullet`: `id`, `tailored_cv_id`, `user_id` (RLS), `kind`,
`location` (summary or experience index + bullet position), `text`,
`status` (`pending` | `linked` | `rejected`), `evidence_ref`,
`decided_at`.

## Interfaces

- **API** `/tailoring` (`apps/api/app/routers/tailoring.py`):
  `GET /candidates` (top jobs by `final_score` for the user),
  `POST /runs` (`job_group_id`), `GET /runs/{id}`,
  `POST /orphans/{id}/decision` (`link` with `evidence_ref`, or `reject`).
  A run is started in the background and reports status the way
  `pipeline.stage_run` does, so the page can poll it.
- **CLI** `tailor-cv --user-id <uuid> --job-group-id <id>` in
  `apps/pipeline/app/cli.py`. It is on-demand, not a batch stage, so it goes
  on the registry's documented exclusion list (with a reason) instead of
  `STAGES`; `test_pipeline_registry` already enforces that every CLI
  subcommand is in one or the other.
- **UI** `apps/ui/app/pages/10_Tailored_CV_Review.py`, using the shared
  theme. A top-jobs picker with a Tailor button; the tailored CV with each
  bullet shown beside its source; pending orphans, each with a source-bullet
  picker and Reject; the stretch warning; keyword coverage.

## Testing

- Unit: assembly (identity of company/title/dates, origin classification),
  each code check, keyword split, orphan decision rules.
- Fake-adapter loop tests: retries stop at two; failures are fed back;
  nothing unchecked is ever `approved`.
- Config test: `fabrication_critic` resolves to `anthropic`.
- Integration (real Postgres, per the project's testing rules): persistence,
  RLS isolation between users, API endpoints.
- **Adversarial, offline:** a fake Tailor deliberately exaggerates (adds a
  metric, a team size, a technology). With a fake critic that applies the
  stated rule it is caught and surfaced; this tests the loop and the
  surfacing logic for free.
- **Adversarial, paid** (`RUN_PAID_TESTS=1`): the same exaggerating Tailor
  against the **real** Claude critic. This is the plan's "done when". It
  makes real Anthropic calls.
- Streamlit AppTest for the page: renders, orphan link/reject paths.

## Out of scope

Rendering to `.docx` or PDF (18a, 18b); cover letters (Step 19); promoting
accepted bullets into the truth base (rejected as a design option, see
*Decisions*); batch auto-tailoring.
Layout-level ATS rules (single column, no tables, headings, date format) — Step 18a.

## Risks and open points

- A local 8B model may tailor poorly. The config makes moving to Claude a
  one-line change; real output quality can only be judged once there is
  output.
- The critic's judgement is itself an LLM judgement. The code checks cover
  everything that can be verified mechanically; the critic covers the
  semantic gap and is exercised by the paid adversarial test.
- 18b depends on a template export that does not exist yet.
