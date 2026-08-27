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

## Idempotency

`jira_key` in `backlog.yml` is written only by `sync_backlog` (via
`python -m jira_sync_kit sync`). Never hand-edit a `jira_key` value — a
story with one is updated in place on the next sync, never recreated.

## Confirm-before-write

Every Jira write triggered by a session (a drafted Bug or a drafted
Story) requires the user's explicit confirmation first. Nothing is
created silently.

## Burndown charts

Native Jira Scrum-board feature — no code involved. Start a sprint on the
project's board once it has issues with story points; the burndown chart
appears under the board's Reports tab automatically.
