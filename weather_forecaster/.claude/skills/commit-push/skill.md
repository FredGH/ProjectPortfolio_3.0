# Commit + Push Skill

Commits the current changes and pushes the branch — auto-invoked when a commit-and-push is requested. Follows GitHub Flow: never pushes directly to `main`/`master`, always onto a feature branch.

## Trigger Conditions

Invoked when the user requests:
- "Commit and push"
- "Commit this and push it up"
- Getting local changes onto the remote branch, without opening a PR

## Workflow

### Step 1 — Branch, if needed

Check the current branch: `git rev-parse --abbrev-ref HEAD`.

**If already on a feature branch** (anything other than `main`/`master`), skip to Step 2.

**If on `main`/`master`:**
1. Sync main first so the new branch isn't based on stale history: `git fetch origin`, then fast-forward if behind (`git status -sb` shows `behind`) — `git merge --ff-only origin/main`. If it can't fast-forward (diverged), stop and ask the user how to reconcile before branching.
2. Pick a branch name: `<type>/<slug>` — `type` is a Conventional-Commit-style prefix (`feat`, `fix`, `chore`, `docs`, `refactor`, `test`) matching the change; `slug` is a short kebab-case description (3-5 words) inferred from the task or the diff. Example: `feat/add-retry-logic`.
3. Create and switch to it: `git checkout -b <type>/<slug>`.

### Step 2 — Commit

Invoke the `commit` skill to run pre-flight gates, stage the relevant files, and create the commit. Do not duplicate its steps here — if it stops on a failing gate, stop too. It now runs on the feature branch, never on `main`/`master`.

### Step 3 — Pre-push gates

- [ ] Confirm we are not on `main`/`master` (Step 1 guarantees this — treat it as a hard stop if somehow still true)
- [ ] Check whether the branch has an upstream yet: `git rev-parse --abbrev-ref --symbolic-full-name @{u}` (fails if unset — first push needs `-u`, which it will on a freshly created branch)
- [ ] Local branch is not behind its remote counterpart (`git fetch` then check `git status`) — if it is, stop and ask whether to rebase/merge before pushing

### Step 4 — Push

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
