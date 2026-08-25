# PR Skill

Opens a pull request from the current branch — auto-invoked when a PR is requested for changes already committed and pushed.

## Trigger Conditions

Invoked when the user requests:
- "Open a PR" / "Create a pull request"

Assumes the current branch's work is already committed and pushed. If it isn't, stop and say so — don't silently commit or push on this skill's behalf; hand off to `commit-push` only if the user confirms that's what they want.

## Workflow

### Step 1 — Pre-flight gates

- [ ] Not on the base branch (`main`/`master`) — a PR needs a feature branch
- [ ] Branch is pushed and up to date with its remote: `git status` shows no unpushed commits and no divergence
- [ ] `gh` is authenticated: `gh auth status`
- [ ] Working tree is clean (no uncommitted changes) — if not, stop and point at the `commit` skill

### Step 2 — Understand the full change set

```bash
git log <base-branch>..HEAD --oneline
git diff <base-branch>...HEAD
```
Review every commit that will be included, not just the latest one.

### Step 3 — Draft title and body

- Title: under 70 characters, summarizing the change
- Body:
```markdown
## Summary
<1-3 bullet points>

## Test plan
<bulleted checklist of how this was/should be verified>
```

### Step 4 — Open the PR

```bash
gh pr create --title "<title>" --body "$(cat <<'EOF'
## Summary
- ...

## Test plan
- [ ] ...
EOF
)"
```

Report the PR URL back to the user when done.

## Usage

```
/pr
```

Also invoked by the `commit-push-pr` skill as its final step.
