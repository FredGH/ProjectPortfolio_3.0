# CLAUDE.md

Team instructions for this project. This file is committed to git and shared with all team members.

## Project Overview

Describe your project purpose, architecture, and key conventions here.

## Tech Stack

- Language: Python 3.11
- Framework: (your framework)
- Database: (your database)

## Development Workflow

1. **Before starting a non-trivial change**, ask once, in a single question: is this a `feat` / `fix` / `chore` / `docs` / `refactor` / `test`, or should it just be made directly without a branch? Skip asking for docs/comment/config-only edits, if already on a non-main branch, or if already answered earlier in this conversation. Branch as `<type>/<slug>`, or `<type>/<JIRA-KEY>-<slug>` if `plan/backlog.yml` exists and `jira-log` resolved a key (see the `commit-push` skill).
2. **If `plan/backlog.yml` exists**, use the `jira-log` skill to record the confirmed fix/feature as a Jira ticket.
3. Implement changes following the rules in `.claude/rules/`
4. Run tests before committing
5. Use the `commit`, `commit-push`, `pr`, or `commit-push-pr` skills to commit/branch/push/open a PR

## Key Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
coverage run -m unittest discover
coverage report -m

# Code quality
ruff check . && isort . && black .
```

## Important Notes

- Follow the Python style rules in `.claude/rules/python-style.md` and SQL style rules in `.claude/rules/sql-style.md`
- Python tests must follow `.claude/rules/python-testing.md`
- SQL/dbt tests must follow `.claude/rules/sql-testing.md`

## Context Management

When compacting, preserve: file paths touched, test/lint commands run, and any pending decisions or TODOs.
