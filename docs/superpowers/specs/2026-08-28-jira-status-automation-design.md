# Jira Status Automation — Design

**Date:** 2026-08-28
**Status:** Draft, pending user review — build deferred to a future session

## Context

The existing Jira integration (`jira_sync_kit` v0.1.0, `docs/superpowers/specs/2026-08-27-jira-integration-design.md`) deliberately never writes Jira's `status` field — it's called out explicitly in that spec's field-ownership contract and Non-goals, and the final whole-branch review specifically verified this invariant holds everywhere in the codebase. That decision stands: this feature does not become a bidirectional sync. `jira_sync_kit` still never *reads* status back from Jira, and a human moving a card in the Jira UI is never overwritten or reconciled against.

What changes: `jira_sync_kit` gains a narrow, one-way, *forward-only* write path for `status`, triggered by objective git events rather than by drafted intent (unlike bug/story creation, which is drafted-then-confirmed). Two behaviors, both requested directly:

1. A story's status advances **To Do → In Progress** when a branch is created for it, and **In Progress → Done** when that branch's PR merges.
2. A sprint completes automatically, but *only* when every issue in it is already Done — if anything is still open, nothing happens; the human completes it manually in the Jira UI (this sidesteps the "what happens to incomplete issues" decision Jira's native Complete Sprint action normally asks a human to make).

Both behaviors were chosen deliberately narrow to avoid reopening the class of problems disjoint ownership was designed to prevent — see Non-goals.

## Non-goals

- **No reading status back from Jira, ever.** `jira_sync_kit` still never queries current status to make decisions beyond the single "is every issue in this sprint Done" check needed for sprint completion — and even that check only ever *reads* to decide whether to fire the one-way "complete sprint" write, never to reconcile.
- **No backward transitions.** If a story is already further along than the event would suggest (e.g. a human already manually moved it to Done, then a branch gets created for a follow-up chore), the automation must never move it backward. Treat "already at or past the target" as a no-op, not an error.
- **No assignee, comments, worklog writes.** Unchanged from the original contract.
- **No per-issue sprint *assignment*.** Which sprint an issue belongs to stays entirely Jira/human-owned, exactly as before — the only new sprint-related write is marking an already-fully-Done sprint as *complete*, a sprint-level action, not a per-issue one.
- **No general-purpose Jira webhook listener.** The trigger is git activity Claude Code already observes during its own session (branch creation, PR merge via the existing skills) — not a running service polling or listening for arbitrary Jira/GitHub events.

## Prerequisite: verify the Agile REST API before implementing

Sprint operations (get active sprint, list a sprint's issues, complete a sprint) live under a **different API surface** — `/rest/agile/1.0/...` — than everything `jira_sync_kit` has used so far (`/rest/api/3/...`, Platform API v3). This needs the same kind of verification the original design did for the Team-Managed project constraint (which turned out to matter): confirm the exact request/response shape for:
- `GET /rest/agile/1.0/board/{boardId}/sprint?state=active`
- `GET /rest/agile/1.0/sprint/{sprintId}/issue`
- How sprint completion is actually triggered (a dedicated action, or a `PUT` on the sprint resource's `state` field — unconfirmed as of this spec)

Do this research before writing the implementation plan, not during it.

## Architecture

### `jira_sync_kit` (new release, ~0.2.0)

| Addition | Role |
|---|---|
| `get_transitions(issue_key) -> list[dict]` | `GET /rest/api/3/issue/{key}/transitions` — the set of transitions actually available from the issue's current state (Jira workflows don't allow arbitrary status writes; you POST a transition ID). |
| `transition_issue(issue_key, target_status_name) -> bool` | Resolves `target_status_name` against `get_transitions()`. If a matching transition exists, POSTs it and returns `True`. If the issue is already at or past the target status (no matching *forward* transition, because it's already there or further along), returns `False` — a no-op, not an error. If the issue is in a state with no path to the target at all, raises `JiraSyncError` (fail loudly, per existing convention — this is a real problem, not the safe no-op case above). |
| `resolve_issue_key_from_branch(branch_name) -> str \| None` | Parses a Jira key out of a branch name matching the new `<type>/<JIRA-KEY>-<slug>` convention (e.g. `feat/JOB-16-repo-scaffold` → `JOB-16`). Returns `None` for branches with no embedded key (e.g. pre-existing branches, or projects without Jira tracking) — callers treat that as "nothing to do," not an error. |
| `get_active_sprint(board_id) -> dict \| None` | Agile API — the currently active sprint on a board, or `None` if none is active. |
| `get_sprint_issues(sprint_id) -> list[dict]` | Agile API — every issue currently in a sprint, with status. |
| `complete_sprint_if_done(sprint_id) -> bool` | Calls `get_sprint_issues`, and only if every one is `Done`, completes the sprint and returns `True`. Otherwise returns `False` — no partial action, no "close it anyway." |

New CLI subcommands: `python -m jira_sync_kit start-story --branch <name>` and `... complete-story --branch <name>` (branch name defaults to the current git branch if omitted). `complete-story` internally calls `complete_sprint_if_done` after transitioning the issue, using the board/sprint resolved from the issue's own sprint field — no separate sprint-id argument needed in the common case.

### Branch-naming convention

`<type>/<slug>` → `<type>/<JIRA-KEY>-<slug>` (e.g. `feat/JOB-16-repo-scaffold`), **conditional on `plan/backlog.yml` existing** — same conditionality pattern the `jira-log` skill and the CLAUDE.md workflow line already use, so projects without Jira tracking are unaffected.

The `jira-log` skill already does the "find the matching story, or propose a new one" work for `feat`/`fix` classifications (with confirmation) — this extends that same step to also surface the resolved `jira_key` for the branch name, rather than building a second matching mechanism. No new "which story is this" logic needed.

### Hook points (existing skills, updated)

| Skill | Change |
|---|---|
| `commit-push` (branch creation) | After creating a branch matching the new convention, silently call `jira_sync_kit start-story` — no confirmation prompt, per the user's explicit choice (objective event, not a judgment call). |
| `pr` / `commit-push-pr` (merge) | After a PR merges, silently call `jira_sync_kit complete-story`, which also fires the sprint-completion check. |

Both call sites must swallow a `None` result from `resolve_issue_key_from_branch` silently (no Jira key in the branch name = nothing to do) but must NOT swallow a `JiraSyncError` from `transition_issue` — surface it, don't block the git operation that triggered it (the branch/merge already succeeded; a Jira-side failure shouldn't be treated as a git failure), but tell the user.

## Data flow

**Story lifecycle:**
`jira-log` skill resolves/creates story with `jira_key` → branch created as `feat/<jira_key>-<slug>` → `commit-push` hook calls `resolve_issue_key_from_branch` → `start-story` → `transition_issue(key, "In Progress")` → PR opens, works, merges → `pr`/`commit-push-pr` hook calls `complete-story` → `transition_issue(key, "Done")` → `complete_sprint_if_done(sprint_id)` (only fires if this was the last open issue in its sprint).

## Error handling

- `transition_issue` raising `JiraSyncError` (genuine failure, not the no-op case) must not block or fail the underlying git operation — the branch/merge already happened. Report the Jira failure to the user as a warning, don't roll back git state.
- Backward transitions are structurally impossible by design (`get_transitions` only returns forward-reachable states from Jira's own workflow graph), not something the client has to defensively check itself beyond returning `False` cleanly for the "already there" case.
- `complete_sprint_if_done` never partially completes — either every issue is Done and it fires, or it's a no-op. No "close anyway" path exists.

## Testing

Same conventions as the rest of `jira_sync_kit`: integration tests against a real (`ZZTEST`) project, gated on `JIRA_*` env vars via `unittest.skipUnless`, no mocking. New coverage needed: `transition_issue`'s no-op-on-already-there case (create an issue, transition it, transition again, confirm second call returns `False` and issue stays put), `resolve_issue_key_from_branch`'s parsing (valid key, no key present, malformed), `complete_sprint_if_done`'s both branches (some issues open → no-op; all Done → completes). The sprint tests need a disposable sprint on the `ZZTEST` project's board, not the real `JOB` sprint.

## Open questions for next session

- Exact Agile REST API shapes (Prerequisite section above) — verify before writing the implementation plan.
- Whether `start-story`/`complete-story` need a `--dry-run` flag given they're now silent/automatic (Task 19's real-sync experience suggests a cheap way to preview an automatic action's effect is worth having before it's genuinely unattended).
- Whether the branch-naming convention change needs a migration note for already-open branches created under the old `<type>/<slug>` format (they simply won't have a resolvable Jira key, which is handled as a silent no-op per the Architecture section — likely fine, but worth confirming no existing branch needs retrofitting).
