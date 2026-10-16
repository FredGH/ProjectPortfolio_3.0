# Step 20 — Interview question bank: design

Status: draft for review · 2026-10-15 · PLAN.md "Step 20"

## Purpose

Give the user an interview question bank they can drill: a shared bank of
technical and behavioural questions with model answers per `qa_category`, a
per-user personal answer and spaced-repetition state, technical questions
ranked by how likely the user is to fumble them (gaps against their CV), and an
Anki export. Generation is a batch the user triggers, never a runtime feature,
and never repeats work already done.

### Done when

- One category's bank generates end to end (batches, critic, storage) and a
  second run for the same category generates nothing.
- A behavioural personal answer drafted from the user's truth base contains no
  untraced sentence (adversarial test, as in Steps 17 and 19).
- The drill queue surfaces due questions first, then the highest-gap technical
  questions; an SM-2 review moves `next_review` as the algorithm says.
- Another user sees none of the first user's answers or progress.

## Decisions taken in brainstorming

| Question | Decision |
|---|---|
| Scope | The full step, generation on demand per category/topic with a cost confirmation |
| Topics | A config file listing topics per `qa_category` (editable), categories read from `category_map.yml` |
| Personal answers | Claude drafts from the user's truth base, the user edits and saves their own version |
| Shared vs per-user | Questions and model answers shared (no RLS, like `intel`); answers, SM-2 state and gap scores per user (RLS, like `tailoring`) |

## Data (migration 0039, schema `interview`)

- **`interview.question_bank`** (shared): `id`, `qa_category`, `topic`,
  `kind` (`technical` | `behavioural`), `difficulty` (1–5), `question`,
  `model_answer`, `tags text[]`, `source` (`generated` now; Step 20a adds
  `asked`), `status` (`approved` | `needs_review` | `rejected`), `critic_issue`,
  `writer_model`, `writer_prompt_version`, `critic_model`,
  `critic_prompt_version`, `created_at`, `last_reviewed`. Unique on
  `(qa_category, topic, question_hash)` so a repeated batch cannot duplicate.
- **`interview.user_question_progress`** (RLS per user): `user_id`,
  `question_id`, `personal_answer`, `answer_status` (`none` | `drafted` |
  `needs_review` | `written`), `ease_factor` (default 2.5), `interval_days`,
  `repetitions`, `next_review`, `last_quality`, `last_reviewed_at`,
  `cv_gap_score`, `truth_base_version_scored`. Primary key `(user_id,
  question_id)`.
- **`interview.personal_answer_open_sentence`** (RLS): the Step 19 open-sentence
  shape for an untraced sentence in a drafted behavioural answer
  (`user_id`, `question_id`, `sentence_id`, `text`, `claimed_sources`, `issue`,
  `status` pending|linked|removed, `evidence_ref`).
- Banks are never regenerated per user. Deleting nothing: a topic is
  regenerated only on an explicit `--force-topic`, which marks the old
  questions `rejected` rather than deleting them.

## Config: `config/interview_topics.yml`

```yaml
target_per_topic: 30
behavioural_per_category: 8
categories:
  data_engineer:
    topics: [SQL and data modelling, Pipelines and orchestration, Cloud data platforms, System design]
  # ... one block per active qa_category
```

The active categories are the distinct non-null values of `qa_category_map` in
`category_map.yml`; a category with no topics block is reported and skipped,
never guessed. Defaults for the five current categories are drafted by hand and
editable.

## Generation (`core/interview/`)

1. **`topics.py`** — load the config and the active categories; work out what is
   missing: a topic is complete when it has `target_per_topic` approved
   technical questions; the category's behavioural set is complete at
   `behavioural_per_category`.
2. **`write.py`** — task `interview_questions` (Claude): one batch of about ten
   questions for `(category, topic, difficulty mix, existing questions to
   avoid)`; the reply is JSON with question, model answer, difficulty and tags.
   Existing question texts are passed in so a batch does not repeat them.
3. **`critic.py`** — task `interview_critic` (Claude only, asserted in code and a
   test, as in Step 19): judges each Q&A for technical correctness (wrong
   claim, outdated practice, misleading answer, question answerable only with
   an invented premise). Unsupported items are regenerated once; those that
   still fail are stored `needs_review` with the critic's issue, never
   `approved`.
4. **`service.py`** — `generate_topic(...)`, `generate_category(...)`;
   idempotent: a complete topic is skipped. A cost estimate comes before any
   call; the CLI and the page confirm it.
5. Behavioural questions (shared, generic prompts) are generated the same way
   with kind `behavioural` and no model answer beyond a short STAR scaffold.

Cost: about three writer batches plus three critic calls per topic.

## Personal answers (`core/interview/personal.py`)

- Behavioural draft: built on the Step 19 sentence-evidence engine. The evidence
  pack is the truth base only (`cv:` ids); sentences cite bullets or roles; code
  checks ids and numbers; the Claude prose critic judges; two retries; anything
  untraced becomes a pending open sentence for the user to link or remove. The
  draft is stored `drafted` (or `needs_review` while sentences are pending).
- The user can edit and save their own text at any time; that is stored as
  `written` and is not re-checked (it is their own words). Drafting again
  replaces a `drafted` answer but never a `written` one without a confirm.
- Technical questions start with `personal_answer` empty; a CV-grounded draft is
  optional and uses the same engine.
- The Step 19 engine is reused through its public functions (`build_pack`,
  `run_checks`, `run_critic`); if its pack or loop need a small generalisation
  (a configurable paragraph structure), that change is part of this step and
  covered by the existing Step 19 tests staying green.

## Gap scoring (`core/interview/gaps.py`, code only, no LLM)

For each approved technical question and user: take the question's `tags`, map
them to canonical skill ids, and compare with the user's truth base — a tag
absent from the CV scores 1.0, present only as a listed skill with no bullet
evidence 0.6, evidenced by at least one bullet 0.2 (and recent use lowers it
further). `cv_gap_score` = mean tag score weighted by difficulty. Stored per
user with the truth-base version it was computed from and recomputed when the
CV version changes. The Gaps tab and the queue use it.

## Drilling (`core/interview/sm2.py`, `queue.py`)

- Standard SM-2: quality 0–5 updates `ease_factor` (floor 1.3), `repetitions`,
  `interval_days` (1, 6, then `interval × ease`; quality < 3 resets) and
  `next_review`. Pure function, fully unit-tested against the textbook cases.
- Queue: due questions (by `next_review`) first, then never-seen technical
  questions by descending gap score, then never-seen behavioural ones. A
  `weight` hook multiplies priority by source (`generated` = 1.0) so Step 20a
  can weight `asked` questions up without a schema change.

## Interfaces

- **CLI** (on demand, excluded from the dashboard registry):
  `generate-question-bank --category X [--topic T] [--force-topic] [--yes]`
  (prints the estimated cost first), `score-question-gaps --user-id U`,
  `export-anki --user-id U --category X [--out FILE]`.
- **API** (shared reads open, per-user via `get_current_user_id`):
  `GET /interview/bank` (status per category/topic),
  `POST /interview/bank/generate` (category, topic; returns counts),
  `GET /interview/queue?category=` , `GET /interview/questions/{id}`,
  `POST /interview/questions/{id}/review` (quality),
  `PUT /interview/questions/{id}/answer` (written answer),
  `POST /interview/questions/{id}/draft` (CV-grounded draft),
  `POST /interview/open-sentences/{id}/decision`, `GET /interview/gaps`,
  `GET /interview/export/anki`.
- **UI:** page **Interview Prep** with Bank, Drill, Gaps and Export; Streamlit
  AppTest coverage like the other pages; a `scripts/demo_interview_ui.sh` and
  slim API for trying it locally.
- **Anki export:** tab-separated, one note per question: front is the question,
  back is the user's personal answer if present else the model answer, plus
  tags; deterministic ordering; a plain `.txt` Anki imports directly.

## Testing

- Unit with fake writer/critic/clock; DB tests on the real dev Postgres with
  `zzfixture` ids; API, CLI, Streamlit tests as before.
- **Adversarial:** a writer scripted to produce a technically wrong answer is
  caught by the critic and stored `needs_review`, never `approved`; a drafted
  behavioural answer with an invented employer or number ends with pending open
  sentences, never `drafted`-clean.
- Idempotency: a second `generate_category` run makes zero LLM calls;
  promoting a new category generates only that category.
- Config: a category with no topics block is reported and skipped; the active
  list is read from `category_map.yml`, not hardcoded (a test adds a category).
- SM-2 maths against the textbook table; queue order; gap scores for the three
  tag situations; recompute on CV version change.
- Isolation: another user sees no personal answers, progress or open sentences;
  shared questions are visible to all.
- Anki export: exact bytes for a small fixture bank.

## Out of scope

Capturing questions actually asked in interviews and weighting them (Step 20a),
tone options, languages other than English, a local-model writer, collaborative
editing of the shared bank, and a rubric eval harness for question quality.

## Plan changes

`PLAN.md` Step 20: now five active categories (not four); tables are
`interview.question_bank`, `interview.user_question_progress`,
`interview.personal_answer_open_sentence`; the behavioural personal-answer draft
reuses the Step 19 evidence engine; `source` and a queue `weight` hook are the
Step 20a seam.

## Risks and open points

- Generation volume (about 600 Q&As, 15–20 writer batches and as many critic
  calls per full run) is a real bill; per-topic confirmation and idempotency
  keep it in the user's hands.
- Tag-to-skill mapping quality limits the gap score; unmapped tags score as
  missing and the page shows which tags could not be mapped.
- The critic checks technical correctness from its own knowledge, not from
  sources, so it can miss subtle errors; `needs_review` and the user's
  `last_reviewed` flag are the safety net.
