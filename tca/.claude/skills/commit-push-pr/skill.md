# Commit + Push + PR Skill

Runs the full flow from local changes to an open pull request — auto-invoked when a commit, push, and PR are all requested together.

## Trigger Conditions

Invoked when the user requests:
- "Commit, push, and open a PR"
- "Ship this as a PR"
- The full commit → push → PR flow in one go

## Workflow

This skill has no steps of its own — it composes the other two:

### Step 1 — Commit and push

Invoke the `commit-push` skill. It runs the `commit` skill first (gates, staging, commit message), then its own pre-push gates and the push.

### Step 2 — Open the PR

Invoke the `pr` skill. It runs its own pre-flight gates, drafts the title/body from the commit log, and runs `gh pr create`.

If either step stops on a failing gate or asks the user a question, stop the whole flow there — do not skip ahead.

## Usage

```
/commit-push-pr
```
