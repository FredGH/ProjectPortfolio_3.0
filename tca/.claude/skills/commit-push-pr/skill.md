# Commit + Push + PR Skill

Runs the full GitHub Flow loop — branch, commit, push, open a PR, and (once it's mergeable) merge and clean up — auto-invoked when the whole flow is requested together.

## Trigger Conditions

Invoked when the user requests:
- "Commit, push, and open a PR"
- "Ship this as a PR"
- "Ship this end-to-end" / "merge it when it's ready"
- The full commit → push → PR → merge flow in one go

## Workflow

This skill has no steps of its own beyond the final merge — it composes the other two:

### Step 1 — Commit and push

Invoke the `commit-push` skill. It branches off `main`/`master` if needed, then runs the `commit` skill (gates, staging, commit message), then its own pre-push gates and the push.

### Step 2 — Open the PR

Invoke the `pr` skill. It runs its own pre-flight gates, drafts the title/body from the commit log, and runs `gh pr create`.

### Step 3 — Merge and clean up

Only after the PR is open:

1. **Wait for CI, if configured.** If `.github/workflows/` exists in the repo, run `gh pr checks --watch`. If any check fails, stop here and report — do not merge a red PR.
2. **Confirm it's mergeable:** `gh pr view --json mergeStateStatus,mergeable`. If there are conflicts or it's otherwise blocked (e.g. required review not yet given), stop and report — do not force through.
3. **Merge:** `gh pr merge --squash --delete-branch`. Squash keeps `main`'s history linear; use a different merge method only if the user asks for one.
4. **Complete the Jira story, if tracked.** If `plan/backlog.yml` exists in this project and the branch merged in step 3 matched `<type>/<JIRA-KEY>-<slug>` (per `commit-push`'s Step 1), silently run:
   ```bash
   python -m jira_sync_kit complete-story --branch <branch-name>
   ```
   using the branch name captured before the merge — `--delete-branch` removes it, so don't rely on `git rev-parse --abbrev-ref HEAD` here. Swallow a "No Jira key in branch ... — nothing to do" result silently. If the command exits non-zero for any other reason, report the failure to the user as a warning — the merge already succeeded, don't roll it back. (To preview what this would do without writing anything, run the same command with `--dry-run` first.)
5. **Sync back:** `git checkout main && git pull`.

If any step stops on a failing gate or asks the user a question, stop the whole flow there — do not skip ahead.

## Usage

```
/commit-push-pr
```
