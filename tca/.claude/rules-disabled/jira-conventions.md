# Jira Conventions

Applies to any project with a `plan/backlog.yml` and the `jira_sync_kit` package installed.

## Issue-type mapping

| Branch-type classification | Jira issue type | Created by |
|---|---|---|
| `feat` | Story (under the matched or a new Epic) | `jira-log` skill, on confirmation |
| `fix` | Bug | `jira-log` skill, on confirmation |
| `chore` / `refactor` / `test` | Sub-task, under the currently active story (see the limitation below) | `jira-log` skill, on confirmation |
| `docs` | No ticket | — |

Note: subtasks (the `chore`/`refactor`/`test` row above) are only
created when their parent story is newly created in the same sync run —
see `jira-log/skill.md` for the current limitation on already-synced
stories.

## Field ownership (disjoint — never violate this)

| `jira_sync_kit` writes | Jira alone owns |
|---|---|
| summary, description | status |
| labels | assignee |
| story points | comments, worklog |
| issue links, subtasks | sprint assignment |

Note: `backlog.yml`'s `acceptance` field is not yet sent to Jira —
`jira_sync_kit` 0.1.0 doesn't have a mapping for it (it goes into
`description` only if you fold it in by hand). Tracked as a follow-up
for a future `jira_sync_kit` release, not implemented as of this table.

Never add code that reads status/assignee/comments/worklog/sprint back
from Jira. This disjoint ownership is what makes the sync safe to re-run
without conflict resolution.

**Exception, added in `jira_sync_kit` 0.2.0:** `jira_sync_kit start-story`/`complete-story`
may write `status` forward-only, triggered by git branch-creation/PR-merge events — never
by session judgment, and never with a confirmation prompt (see the design spec at
`docs/superpowers/specs/2026-08-28-jira-status-automation-design.md` in the ProjectPortfolio_3.0
repo root). This one-way write is the only exception to "never reads status/.../sprint back"
above: `jira_sync_kit` reads an issue's current `status` (to gate the forward-only transition)
and its `sprint` (to resolve which sprint to check for completion) — but strictly to decide
whether to fire that single write, never to reconcile prior state, never to move status
backward. A human moving a card in the Jira UI is never overwritten, and no other field
(assignee, comments, worklog, or per-issue sprint *assignment*) is ever read or written.

## Idempotency

`jira_key` in `backlog.yml` is written only by `sync_backlog` (via
`python -m jira_sync_kit sync`). Never hand-edit a `jira_key` value — a
story with one is updated in place on the next sync, never recreated.

## Branch naming

If `plan/backlog.yml` exists, branches for tracked stories use
`<type>/<JIRA-KEY>-<slug>` (e.g. `feat/JOB-16-repo-scaffold`) instead of the plain
`<type>/<slug>`. Pre-existing branches created before this convention simply have no
embedded Jira key — `resolve_issue_key_from_branch` returns `None` for them, treated as a
silent no-op. No migration is needed.

## Confirm-before-write

Every Jira write triggered by a session (a drafted Bug or a drafted
Story) requires the user's explicit confirmation first. Nothing is
created silently.

**Exception:** the narrow, git-event-triggered status writes (`start-story`/`complete-story`,
and the sprint-completion check they trigger) are silent by design — the triggering event
(branch creation, PR merge) is itself the confirmation. This applies only to those two
automated calls, never to a drafted Bug or Story.

## Burndown charts

Native Jira Scrum-board feature — no code involved. Start a sprint on the
project's board once it has issues with story points; the burndown chart
appears under the board's Reports tab automatically.
