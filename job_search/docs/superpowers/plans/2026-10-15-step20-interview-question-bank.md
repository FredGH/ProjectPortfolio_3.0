# Step 20 — Interview question bank: implementation plan

> Executed natively (same session), task by task, test first. Spec:
> [../specs/2026-10-15-step20-interview-question-bank-design.md](../specs/2026-10-15-step20-interview-question-bank-design.md)

**Goal:** a shared interview question bank per `qa_category` (generated on
demand, critic-checked, idempotent), per-user personal answers (CV-grounded
drafts through the Step 19 evidence engine), CV-gap scoring, SM-2 drilling and
Anki export, with CLI, API and a Streamlit page.

**Stack:** as Step 19 (Python 3.11, SQLAlchemy/Alembic, RLS, pydantic, FastAPI,
Streamlit, unittest; Claude via `core.company_intel.llm.run_task`).

## Global constraints
- Same commands, formatting (`../.superpowers/fmt.sh` from packages/core), docstring and test rules as the Step 19 plan; commit trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Active categories = distinct non-null values of `qa_category_map` (`core.classification.classify.load_qa_category_map`); never hardcoded.
- The bank critic (`interview_critic`) is Claude-only, asserted in code and test. A question is `approved` only if the critic judged it supported.
- Shared tables (`question_bank`) have no RLS; per-user tables use `app.current_user_id` RLS. Nothing deletes questions; regeneration marks the old ones `rejected`.
- Generation is on demand and asks for confirmation of the estimated cost (CLI `--yes` skips only the prompt, not the estimate print).

## Tasks (each ends with a green test run and a commit)
1. **Migration 0039** `interview` schema: `question_bank`, `user_question_progress`, `personal_answer_open_sentence`; schema/RLS/enum test.
2. **Config and topics**: `config/interview_topics.yml`, `core/interview/config.py` (load, active categories, missing work), tests incl. "adds a category" and "no topics block is reported".
3. **SM-2 and queue** (pure): `sm2.py`, `queue.py` with source `weight` hook; textbook-case tests.
4. **Gap scoring** (pure): `gaps.py` tag-vs-truth-base scoring (absent 1.0, listed-only 0.6, bullet-evidenced 0.2), aliases from `config/skill_aliases.yml`; tests.
5. **Bank generation**: tasks `interview_questions`, `interview_critic`; prompts; `write.py`, `critic.py`, `store.py`, `service.py` (`generate_topic`, `generate_category`, idempotent, cost estimate); unit tests with scripted adapters (wrong answer caught, regenerate once, then `needs_review`; second run zero calls) and DB tests.
6. **Personal answers**: expose `check_sentences`/`build_sentence` from the Step 19 checks/assemble (existing tests stay green); `personal.py` draft via evidence engine, open sentences, decisions; tests (invented employer/number → pending, never clean-drafted; written answer never overwritten without confirm).
7. **Progress service + Anki**: queue/review/answer/gaps persistence, `anki.py`; DB tests incl. isolation and exact export bytes.
8. **CLI**: `generate-question-bank`, `score-question-gaps`, `export-anki`; excluded from the dashboard registry.
9. **API**: `/interview/*` router; router tests.
10. **UI**: `Interview Prep` page (Bank, Drill, Gaps, Export) + AppTest; `scripts/demo_interview_ui.sh` + slim API.
11. **Docs and live check**: README/PLAN notes; real-Claude generation of one small topic and one behavioural draft; whole-branch review; PR.

## Review focus
1. A technically wrong model answer is never `approved` (critic miss or unreadable critic → not approved).
2. A second generation run makes zero LLM calls; a new category generates only itself.
3. Another user never sees personal answers, progress, or open sentences.
4. A behavioural draft with an invented employer/number is never clean; a user-written answer is never silently replaced.
5. Prompt-injection text in a CV bullet or an existing question is data only.
