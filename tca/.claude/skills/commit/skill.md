# Commit Skill

Stages relevant changes and creates a well-formed commit — auto-invoked when a commit is requested.

## Trigger Conditions

Invoked when the user requests:
- Committing the current changes
- "Commit this" / "make a commit"
- Wrapping up work into a commit, without pushing or opening a PR

## Workflow

### Step 1 — Pre-flight gates

- [ ] No linting errors: `ruff check .`
- [ ] No formatting issues: `black --check .`
- [ ] Tests pass: `coverage run -m unittest discover`
- [ ] No secrets in the diff: `git diff | grep -iE "password|secret|token|api_key"` returns nothing suspicious
- [ ] `git status` reviewed — know exactly which files are changed/untracked before staging

If any gate fails, stop and report it. Do not commit through a failing gate.

### Step 2 — Stage changes

Stage only the files relevant to this change, by name. Avoid `git add -A` / `git add .` — an unrelated or sensitive file (`.env`, credentials, stray build output) can get swept in.

After staging, run `git status` again and confirm the staged set matches intent.

### Step 3 — Draft the commit message

Follow Conventional Commits format:
```
<type>(<scope>): <short summary>

<body — the "why", not the "what"; omit if the summary says it all>
```
Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `build`, `ci`.

Check `git log --oneline -10` first and match the repo's existing style if it diverges from this convention.

### Step 4 — Commit

```bash
git commit -m "$(cat <<'EOF'
<type>(<scope>): <summary>

<body>
EOF
)"
```

Only commit when the user has asked for a commit — never as a side effect of another task.

## Usage

```
/commit
```

Also invoked by the `commit-push` and `commit-push-pr` skills as their first step.
