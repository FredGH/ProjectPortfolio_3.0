# Commit + Push Skill

Commits the current changes and pushes the branch — auto-invoked when a commit-and-push is requested.

## Trigger Conditions

Invoked when the user requests:
- "Commit and push"
- "Commit this and push it up"
- Getting local changes onto the remote branch, without opening a PR

## Workflow

### Step 1 — Commit

Invoke the `commit` skill to run pre-flight gates, stage the relevant files, and create the commit. Do not duplicate its steps here — if it stops on a failing gate, stop too.

### Step 2 — Pre-push gates

- [ ] Not pushing directly to `main`/`master` — if the current branch is the base branch, confirm with the user before pushing
- [ ] Check whether the branch has an upstream yet: `git rev-parse --abbrev-ref --symbolic-full-name @{u}` (fails if unset — first push needs `-u`)
- [ ] Local branch is not behind its remote counterpart (`git fetch` then check `git status`) — if it is, stop and ask whether to rebase/merge before pushing

### Step 3 — Push

```bash
# First push on a new branch
git push -u origin <branch-name>

# Subsequent pushes
git push
```

Never force-push (`--force`) unless the user explicitly asks for it.

## Usage

```
/commit-push
```

Also invoked by the `commit-push-pr` skill as its first step.
