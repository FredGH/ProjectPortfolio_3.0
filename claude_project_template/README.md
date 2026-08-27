# Claude Project Template

A reusable Claude Code project scaffold: conventions, agents, slash commands, and
skills for a Python 3.11 data-engineering project (dbt/SQL included). Copy this
directory as the starting point for a new project rather than building the
`.claude/` setup from scratch each time.

> **Note:** this README documents the *template itself* — it is not part of
> what gets copied into sibling/derived projects. See
> [Using this as a template](#using-this-as-a-template) below.

## What's here

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Team instructions — committed to git, shared with everyone working in the project |
| `CLAUDE.local.md` | Personal overrides — gitignored, never shared |
| `.claude/settings.json` | Team-wide permissions and hooks (committed) |
| `.claude/settings.local.json` | Personal permission overrides (gitignored) |
| `.claude/rules/` | Style and testing conventions Claude follows automatically |
| `.claude/agents/` | Specialized subagents for isolated-context review work |
| `.claude/commands/` | Slash commands |
| `.claude/skills/` | Auto-invoked workflows, triggered by natural-language phrasing |

## Rules (`.claude/rules/`)

- `python-style.md` — formatting, naming, docstrings, type hints
- `python-testing.md` — `unittest` + `coverage` conventions, no DB mocking
- `sql-style.md` — SQL/dbt formatting, naming, CTE and comment conventions
- `sql-testing.md` — dbt schema tests, singular tests, severity policy

## Agents (`.claude/agents/`)

- `code-reviewer` — general code review, isolated context
- `python-reviewer` — Python-specific correctness and idioms
- `sql-reviewer` — SQL/dbt query correctness, performance, modeling
- `security-auditor` — full OWASP-style threat modeling pass
- `python-security-auditor` — Python-specific attack surface
- `sql-security-auditor` — injection, privilege, data exposure risks in SQL/dbt

## Commands (`.claude/commands/`)

- `/review` — thorough review of current changes or a specified file/PR
- `/api-review` — checks against the project's API conventions
- `/deploy` — runs deployment steps for a specified environment
- `/fix-issue` — takes an issue description or number and applies a fix

## Skills (`.claude/skills/`)

Skills auto-invoke based on what you ask for — no slash command needed.

- **`deploy`** — structured pre-deploy gates → build → stage → promote → verify checklist
- **`docker-deploy`** — builds a Docker image and deploys to Hugging Face Spaces or AWS ECS/Fargate
- **`security-review`** — routes changed files to the right security-auditor agent by file type
- **`commit`** — stages relevant files, drafts a Conventional Commits message, commits
- **`commit-push`** — GitHub Flow: branches off `main` automatically (`<type>/<slug>`) if needed, then commits and pushes
- **`pr`** — opens a PR from an already-pushed branch, with a drafted title/body
- **`commit-push-pr`** — the full loop: branch → commit → push → PR → (once mergeable) squash-merge and clean up
- **`backup-local-config`** — syncs gitignored `.local.` files (personal overrides) into a separate private backup repo

`commit-push` and `commit-push-pr` are composed explicitly from the smaller skills (each says "invoke the `commit` skill," etc.) rather than duplicating their steps — read any one `skill.md` file to see the exact chain.

## Using this as a template

To start a new project from this template:

1. Copy the whole directory, **except this `README.md`**, into the new project's location and rename it.
2. Update `CLAUDE.md`'s Project Overview, Tech Stack, and Key Commands sections for the new project.
3. Fill in `CLAUDE.local.md` with the new project's local setup details.
4. Adjust `.claude/settings.json` permissions if the new project needs Bash access beyond `git`/`python`/`pip`/`ruff`/`black`/`isort`/`coverage`.

## Keeping sibling projects in sync

Existing projects that were already scaffolded from this template
(`complaint_analyser`, `cortex_signal_to_action`, `job_search`, `playground`,
`research_to_podcast`, `tca`, `weather_forecaster`) get template improvements
propagated to them over time — copying the relevant `.claude/` files across,
never this README. A hook in the parent repo's
`ProjectPortfolio_3.0/.claude/settings.local.json` (personal, gitignored, not
part of this template) reminds Claude to ask before porting a template change
to those projects whenever a file under `claude_project_template/` is edited.
