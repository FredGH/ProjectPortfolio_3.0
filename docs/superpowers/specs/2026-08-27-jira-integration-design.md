# Jira Integration — Generic Template + Per-Project Design

**Date:** 2026-08-27
**Status:** Draft, pending user review

## Context

`job_search/PLAN.md` (Step 0) and `job_search/DECISIONS.md` (§2.15–2.16)
already specify a one-way, one-shot Jira sync: `job_search/backlog.yml` is
canonical, `plan/sync_jira.py` (not yet written) pushes it into Jira, and the
two systems never write the same field. That design explicitly rejects
auto-filing bugs ("becomes a backlog of noise you eventually bulk-close").

This spec extends that design in three ways the user asked for, and
generalizes it so every sibling project under `ProjectPortfolio_3.0/` can
adopt Jira tracking without re-deriving the integration:

1. Ad-hoc bug fixes and net-new feature requests, discovered mid-session,
   get drafted as Jira tickets and created **after explicit confirmation**
   — reversing the "never auto-file" decision, but with a human gate that
   avoids the noise outcome the original decision was guarding against.
2. The generic mechanics are reusable rather than re-derived per project —
   see **Reuse mechanism** below for the decision and why.
3. The target Jira **project** (e.g. `JOB` for job_search) is created
   automatically if it doesn't exist yet, scoped correctly so every epic,
   story, subtask, and ad-hoc ticket lands under the right project.

Burndown charts require no code: Jira draws them natively from a Scrum
board once issues carry story points and sit in a started sprint. This is
a one-time manual Jira UI step per project, not part of this build.

**Verified against current Atlassian docs/community sources (2026-08-27):**
the Free plan has full, unrestricted REST API access (confirmed on
Atlassian Community). The one real constraint: `POST /rest/api/3/project`
**cannot create Team-Managed projects** — only Company-Managed (classic)
ones support API-driven creation. Team-Managed is the UI default, so
`ensure_project()` must explicitly request a company-managed template
(`projectTemplateKey: com.pyxis.greenhopper.jira:gh-scrum-template`).
Company-managed projects support sprints/story points/burndown exactly
like team-managed ones, so nothing is lost — see sources at the end.

## Reuse mechanism

`claude_project_template/` already generalizes Claude-Code config
(rules/agents/commands/skills) via copy-propagation — the README documents
that a template improvement is manually re-copied into each of the 7
sibling projects over time. That works for markdown instructions because
drift is cheap to eyeball and fix. It's a worse fit for real Jira REST
logic: a bug in retry/pagination/error handling would need the same
manual re-propagation, but silently, into code nobody re-reads line by
line the way a skill.md gets read.

| | Considered | Rejected because |
|---|---|---|
| **Chosen** | Installable package (`jira_sync_kit`), `pip install git+https://github.com/FredGH/jira_sync_kit.git@<tag>` | — matches the existing `my_package` convention already documented in the root `CLAUDE.md` for exactly this situation: code shared across sibling projects. One codebase; a fix or new feature lands once and every project upgrades by bumping its pinned tag. |
| Alternative | Copy `jira_client.py`/`sync_jira.py` into `claude_project_template/`, propagate like `.claude/` files | Real application code drifts silently across 7 siblings the same way `.claude/` files already require manual re-propagation — acceptable friction for markdown, a correctness risk for a client that talks to a paid-adjacent external API. |

Net split: **package** owns all Jira REST logic (`client.py`, `sync.py`,
CLI, tests) — nothing project-specific in it. **`claude_project_template/`**
owns only the Claude-Code-specific glue that has no packaging equivalent:
the `jira-log` drafting skill and the `jira-conventions` rule. Each
sibling project owns just its `backlog.yml` content, `.env` credentials,
and a pinned version of the package.

## Non-goals

- No two-way sync. Status, assignee, comments, worklog, sprint stay
  Jira-owned, exactly as the original design specifies.
- No automated Jira **site** (Atlassian tenant) creation — that's a
  one-time human signup per Atlassian account, not API-automatable without
  an existing site to call from.
- No mining of application logs to auto-file bugs. Only the drafting flow
  described here (developer-facing, mid-session, confirmed) creates bugs.

## Prerequisite: Jira Free site & API token

The one piece of this design that's genuinely a human, not automatable —
called out in Non-goals above. Needed before any of `client.py`'s
integration tests can run for real (they skip gracefully without it, per
Testing below, but that only proves the code imports, not that it works).

**1. Create the Jira Free site**
- Sign up at atlassian.com/software/jira/free (or id.atlassian.com if
  there's no Atlassian account yet).
- Picking a site name fixes `JIRA_SITE_URL`: `https://<sitename>.atlassian.net`.
- Jira's onboarding may prompt for a first project — skip or cancel it.
  `ensure_project()` creates projects programmatically (a `ZZTEST`
  project for `jira_sync_kit`'s own tests, `JOB` for job_search later),
  so nothing needs to be created by hand.

**2. Generate an API token**
- `id.atlassian.com/manage-profile/security/api-tokens` → Create API
  token → label it (e.g. `jira_sync_kit`).
- Copy the value immediately — Atlassian shows it once, never again.

**3. Record three values**
- `JIRA_SITE_URL` — from step 1.
- `JIRA_EMAIL` — the Atlassian account's sign-up email, used for Basic Auth.
- `JIRA_API_TOKEN` — from step 2.

**4. Place them in `.env`, never committed**
`jira_sync_kit`'s own integration tests read these via `python-dotenv`
from a `.env` in its working directory (`.gitignore`d — see Architecture).
job_search's later adoption (`.env` per the Architecture table below)
reuses the same three values plus an optional
`JIRA_TEST_PROJECT_KEY=ZZTEST` to keep the package's own tests off the
real `JOB` project.

## Architecture

### Package — new repo, e.g. `FredGH/jira_sync_kit` (installed, not copied)

Mirrors how `my_package` and other private packages are already consumed
(root `CLAUDE.md`: `pip3.11 install git+https://github.com/FredGH/<pkg>.git@<tag>`).
This is the single source of truth for all Jira REST logic — fixed once,
upgraded everywhere by bumping a tag.

| Path | Role |
|---|---|
| `jira_sync_kit/client.py` | `ensure_project(key, name, template_key="com.pyxis.greenhopper.jira:gh-scrum-template")`, `create_or_update_issue(...)`, `create_bug(...)`, `link_blocks(...)`. Resolves the acting user's `accountId` via `GET /rest/api/3/myself` for project lead — no hardcoded lead. Company-managed template by default, since Team-Managed can't be created via API. |
| `jira_sync_kit/sync.py` | Thin driver: calls `ensure_project()` once, then walks a project's `backlog.yml` epics → stories → subtasks via `client.py`, writing `jira_key` back into the YAML. Idempotent — a story with a `jira_key` is updated in place, never recreated. |
| `jira_sync_kit/__main__.py` | CLI entry point (`python -m jira_sync_kit ensure-project`, `create-bug`, `create-story`, `sync`) so both a project's own thin wrapper and the drafting skill call it without duplicating Jira REST logic. |
| `backlog.example.yml` (package data) | Annotated skeleton: `meta` block (`project_key`, `project_name`, `project_template`, `issue_types`, `label_vocabulary`, `point_scale`) plus one example epic/story/subtask. Exposed via `python -m jira_sync_kit init` to scaffold a new project's backlog. |
| Tests | Integration tests against a real Jira project, gated on `JIRA_*` env vars — see Testing below. |

Repo location and privacy (private, alongside the user's other packages)
need the user's go-ahead before creation — this is a new GitHub repo, not
just a file in an existing one.

### Generic — `claude_project_template/` (still copied, Claude-Code glue only)

| Path | Role |
|---|---|
| `.claude/skills/jira-log/skill.md` | Drafting workflow. On a confirmed `fix`: draft a Bug (summary/description/labels), show it, on confirmation run `python -m jira_sync_kit create-bug` (which calls `ensure_project()` first). On a confirmed `feat`: draft a Story/Task, find or propose the right epic, on confirmation append to `backlog.yml` and run `python -m jira_sync_kit sync`. No-ops if the project has no `plan/backlog.yml`. |
| `.claude/rules/jira-conventions.md` | Issue-type mapping (`feat`→Story, `fix`→Bug, `chore`/`refactor`/`test`→Sub-task under the active story, `docs`→none), disjoint field-ownership contract, idempotency rule, confirm-before-write policy, note that burndown is native Jira config. |
| `CLAUDE.md` (template) | One added line in Development Workflow: *"If `plan/backlog.yml` exists, use the `jira-log` skill to record confirmed fixes/features."* Plus a note under Python Environment Setup: install `jira_sync_kit` alongside the project's own dependencies. |
| `.env.example` | Adds `JIRA_SITE_URL=`, `JIRA_EMAIL=`, `JIRA_API_TOKEN=` placeholders. |
| `README.md` | Documents the new files under "What's here" and adds a template-setup step for projects that want Jira. |

### Specific — per sibling project (job_search first)

| Path | Role |
|---|---|
| `plan/backlog.yml` | Real content — job_search's existing 396-item backlog, relocated from repo root into `plan/` per the layout `PLAN.md` already specifies. `meta.project_key: JOB`, `meta.project_name`, label vocabulary, point scale — all project-specific. |
| `.env` (gitignored) | Real `JIRA_SITE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`. |
| `jira_sync_kit` install | Added to the project's own `pip install -e .` / requirements, pinned to a tag. |
| Jira Free site | One-time manual Atlassian signup — not automated. |

Any other sibling that wants this later installs `jira_sync_kit`, copies
`.claude/skills/jira-log/` and `.claude/rules/jira-conventions.md` from
the template, and writes its own `plan/backlog.yml` from
`python -m jira_sync_kit init`. No Jira REST logic is duplicated per
project — only config and credentials.

## Data flow

**One-time / re-runnable (planned backlog):**
`backlog.yml` → `python -m jira_sync_kit sync` → `client.ensure_project()`
→ walk epics/stories/subtasks → create-or-update by `jira_key` → write
`jira_key` back to YAML → link `blocks` dependencies.

**Ongoing (ad-hoc, mid-session):**
`fix`/`feat` classification (existing CLAUDE.md step 1) → `jira-log` skill
drafts ticket → user confirms → skill runs `python -m jira_sync_kit`
directly (bugs) or appends to `backlog.yml` then runs `... sync`
(features).

Both paths converge on the same installed package, so `ensure_project()`
is written once and is safe to call repeatedly (idempotent
GET-then-create).

## Error handling

- `client.py` calls fail loudly (non-zero exit, printed Jira error body)
  — no silent retries or swallowed failures, consistent with
  `python-style.md`'s "no bare except" rule.
- If `ensure_project()` fails (e.g. insufficient permissions on the Jira
  site), `sync.py` and the `jira-log` skill both stop before writing
  anything — never partially create issues against a project that failed
  to provision.
- A failed `create-bug`/`create-story` call leaves `backlog.yml` untouched
  (write only after a confirmed successful Jira response) and reports the
  failure back to the user in-session — no queued retry.

## Testing

Per `python-testing.md`, external API calls are tested with real
connections, not mocks. `jira_sync_kit` ships its own integration tests
against a real (free-tier) Jira project, gated behind the presence of
`JIRA_*` env vars (`unittest.skipUnless`) so they no-op in environments
without credentials rather than failing CI. Cover: `ensure_project()`
requests a company-managed template and is idempotent (second call is a
no-op GET), issue creation round-trips a `jira_key`, and a bug created via
the CLI lands under the correct project key.

## Open questions for user review

None outstanding. The package repo is created:
[`FredGH/jira_sync_kit`](https://github.com/FredGH/jira_sync_kit)
(private), scaffolded with only a `README.md` and `.gitignore` — no
implementation yet. Everything else reflects answers already given:
Claude-drafts/user-confirms for both bugs and features, features append
to `backlog.yml` before syncing, bugs go directly to Jira, Jira project
creation is automatic (company-managed template, per the Free-plan API
constraint above).

## Sources

- [Solved: Use REST API with Jira instance on Free Plan?](https://community.atlassian.com/forums/Jira-questions/Use-REST-API-with-Jira-instance-on-Free-Plan/qaq-p/2955396)
- [REST API - Can I create Team managed project with custom workflow and statuses?](https://community.atlassian.com/forums/Jira-questions/REST-API-Can-I-create-Team-managed-project-with-custom-workflow/qaq-p/2367907)
- [Jira Free plan, 10-user limit](https://community.atlassian.com/forums/Jira-questions/Jira-Free-plan-10user-limit/qaq-p/3185094)
