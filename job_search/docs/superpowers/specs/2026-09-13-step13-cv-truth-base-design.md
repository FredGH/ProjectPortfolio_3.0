# Step 13 — CV Truth Base (JOB-202) — Design

## Context

PLAN.md Step 13 asks for one canonical, structured representation of a
user's CV — the "truth base" every later step (skill normalisation,
scoring, tailored-CV generation, the fabrication critic) reads from
instead of re-parsing a PDF. Steps 5a–12 are done; this is the first
step in the "per-user artefact" half of the plan.

Per PLAN.md: skip naive PDF text extraction (CVs are multi-column,
table-heavy, layout-dependent) in favour of Docling for PDF→markdown,
then LLM structured extraction against a Pydantic schema. Every bullet
gets a stable ID — the thing Step 17's fabrication guard checks a
generated bullet against.

## Scope

**In scope:**
- The Pydantic truth-base schema
- Docling PDF→markdown extraction
- LLM structured extraction (markdown → schema), routed per
  DECISIONS.md's task-split table
- Deterministic, stable bullet IDs
- Two-table versioned storage (`cv_truth_base` current +
  `cv_truth_base_history` append-only), one base CV per user enforced
  by a DB UNIQUE constraint
- A Streamlit correction pass UI
- Wiring `cv_extraction` into Step 12a's eval harness (`config/llm_tasks.yml`
  entry + a synthetic golden set)

**Explicitly deferred** (backlog.yml lists this as a JOB-202 subtask;
user agreed to defer it to a later pass rather than drop it):
- Falling back to a second extractor and diffing the two outputs when
  confidence is low. `extracted_markdown` is still stored (cheap, and
  needed regardless — see "Extraction pipeline" below), which is what
  makes adding this later a diff-logic addition, not a re-extraction.

**Out of scope:** Skill normalisation via ESCO (Step 14) — the truth
base's `skills[].canonical_id` field exists in the schema but is
populated `None` until Step 14 runs.

## Handling the real CV used for verification

This design is verified interactively against the user's real CV
(`Frederic_Marechal_2026_v3.pdf`) during implementation, to confirm
Docling's extraction actually works on a real, messy document layout
(the CV's Education section uses a visual two-column block that plain
text extraction would scramble). That file and anything derived from
its real content:
- Is **never** committed to the repository, in any form (fixture,
  golden-set case, screenshot, log excerpt).
- Is **never** used as the source for the `cv_extraction` golden set —
  every golden case is synthetic, invented CV snippets with no real
  personal data.
- May be loaded into the local dev database during interactive testing
  (that's what a per-user truth-base table is for), but is not part of
  any committed test fixture or migration seed data.

## Schema

New module `packages/core/core/cv/schema.py`, Pydantic v2 models:

```python
class Bullet(BaseModel):
    bullet_id: str          # stable, deterministic — see below
    text: str

class Experience(BaseModel):
    company: str
    title: str
    start: str              # "YYYY-MM" — CVs rarely give a day
    end: str | None         # None means "present"
    bullets: list[Bullet]
    tech: list[str]
    metrics: list[str]

class Skill(BaseModel):
    name: str
    canonical_id: str | None    # ESCO ID — populated by Step 14, None until then
    years: float | None
    last_used: str | None       # "YYYY-MM"
    evidence_refs: list[str]    # bullet_ids this skill is evidenced by

class Education(BaseModel):
    institution: str
    qualification: str
    start: str | None
    end: str | None

class Certification(BaseModel):
    name: str
    year: int | None

class Publication(BaseModel):
    citation: str

class CVTruthBase(BaseModel):
    identity: str            # full name
    headline: str            # e.g. "Senior Data Engineer"
    locations: list[str]
    work_auth: str | None
    skills: list[Skill]
    experience: list[Experience]
    education: list[Education]
    certifications: list[Certification]
    publications: list[Publication]
```

### Stable bullet IDs

`bullet_id = sha256(f"{experience_index}:{normalize(text)}")[:16]`,
where `normalize` lowercases and collapses whitespace. Deterministic
over the bullet's own content and position, not over any extraction
run's incidental ordering elsewhere — re-extracting the same CV
reproduces the same IDs for unchanged bullets, which is exactly the
"survives re-extraction" bar PLAN.md's "Done when" sets. A bullet whose
*text* changes (a correction, or a materially different re-extraction)
gets a new ID — that is correct: it is evidence of different content,
and Step 17's fabrication guard should not treat edited text as the
same claim it originally checked.

## Storage

Two tables, per-user (RLS-enforced, following the `user_quota` pattern
from migration 0002 — `ENABLE ROW LEVEL SECURITY` + a policy on
`current_setting('app.current_user_id', true)::uuid`):

```sql
CREATE TABLE cv_truth_base (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES app_user(id),
    version INT NOT NULL,
    extracted_markdown TEXT NOT NULL,
    truth_base JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cv_truth_base_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES app_user(id),
    version INT NOT NULL,
    extracted_markdown TEXT NOT NULL,
    truth_base JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, version)
);
```

`user_id UNIQUE` on `cv_truth_base` is the DB-enforced "exactly one
base CV per user" PLAN.md asks for — no application-level check that
an import path or fixture could bypass.

Replacing a truth base (a fresh extraction, or a correction-UI save) is
one transaction: insert the new row into `cv_truth_base_history` at
`version = current + 1`, then `UPDATE ... SET version, extracted_markdown,
truth_base, updated_at` on the single `cv_truth_base` row (or `INSERT`
if this is the user's first CV). Never a second live row, and the
version number is only ever incremented, never reused.

## Extraction pipeline

`packages/core/core/cv/extract.py`:

1. `docling_to_markdown(pdf_bytes: bytes) -> str` — Docling's
   `DocumentConverter`, converting the PDF to markdown. This is the
   step verified against the real CV.
2. `extract_truth_base(markdown: str, *, adapters, provider, model,
   prompt_family) -> CVTruthBase` — one LLM call via
   `core.llm.gateway.complete`, task `"cv_extraction"`, prompt loaded
   from the registry (`prompts/cv_extraction/local.v1.md` — this task
   runs on Ollama per DECISIONS.md's task split: "CV truth-base
   extraction: local (hand-corrected anyway)"). The response is parsed
   as JSON and validated against `CVTruthBase`.
3. `write_truth_base(engine, user_id, markdown, truth_base)` — the
   history-then-replace transaction above.

Storing `extracted_markdown` (already in scope per PLAN.md, independent
of the deferred diff feature) means a schema fix or a re-run of step 2
alone never requires re-running Docling.

## Eval harness wiring (Step 12a integration)

- `config/llm_tasks.yml` gets a `cv_extraction` entry:
  ```yaml
  cv_extraction:
    provider: ollama
    model: llama3.1:8b
    prompt_family: local
    eval_metric: field_f1
    eval_regression_threshold: 0.05
  ```
- A new file-based golden set, `evals/golden/cv_extraction.yml`: at
  least 20 cases (`runner.py`'s `MINIMUM_GOLDEN_SET_SIZE`), each a
  short **synthetic** markdown CV snippet as `input` and a flat
  expected-fields dict (e.g. `company`, `title`, `start`, `end`, one
  bullet's `text`, one `skill_name`) as `expected`, scored by
  `field_f1`. Golden cases test the markdown→schema LLM step only —
  Docling's PDF→markdown fidelity has no automated golden-set coverage
  in this pass (it's verified once, interactively, per "Handling the
  real CV" above); adding it later means writing synthetic PDF
  fixtures, which is a real but separate effort.
- A new predictor, `_predict_cv_extraction`, registered in
  `runner.py`'s `_PREDICTORS`, calling `extract_truth_base` on the
  case's markdown input and flattening the result to match the
  golden case's expected keys.

## Correction UI

New Streamlit page, `apps/ui/app/pages/5_CV_Correction.py`, following
the existing Categorisation Review page's pattern: loads the current
user's `cv_truth_base`, renders editable fields per experience/skill
entry, and on save calls the same history-then-replace write path as
extraction (so a manual correction is version `N+1` exactly like a
re-extraction would be — "version the truth base so CV edits are
traceable").

New API router, `apps/api/app/routers/cv.py`:
- `GET /cv/truth-base` — the current user's `cv_truth_base` (401/501
  until Step 22a's auth lands, via the existing `get_current_user_id`
  dependency — same seam every other per-user endpoint already uses).
- `PUT /cv/truth-base` — replace it (used by both the correction UI
  save and, indirectly, a fresh extraction).
- `POST /cv/extract` — upload a CV PDF, run the extraction pipeline,
  write the result as a new version.

## Testing

- Unit tests for `Bullet` ID determinism (same text/position → same
  ID; changed text → different ID), schema validation, and the
  markdown-normalisation helper.
- Integration tests (real Postgres, per this repo's testing rules) for
  the history-then-replace transaction: confirms the UNIQUE constraint
  holds, confirms history accumulates, confirms RLS isolates users.
  Uses synthetic fixture data only.
- Golden-set cases for `cv_extraction` are the eval-harness coverage
  for extraction quality — not duplicated as separate unit tests.
- Docling's real-CV behavior is verified manually in this session (not
  an automated test, since the input can't be committed).

## Done when

The CV round-trips to JSON and back (markdown → `CVTruthBase` →
`cv_truth_base` row → re-read as the same `CVTruthBase`), and every
bullet's `bullet_id` survives a re-extraction of unchanged text.
`run-evals cv_extraction` reports a real score (not
`insufficient_data`) against the synthetic golden set.
