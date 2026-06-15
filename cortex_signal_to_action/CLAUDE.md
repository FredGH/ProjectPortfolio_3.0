# CLAUDE.md

Team instructions for this project. This file is committed to git and shared with all team members.

## Project Overview

Describe your project purpose, architecture, and key conventions here.

## Tech Stack

- Language: Python 3.11
- Framework: (your framework)
- Database: (your database)

## Development Workflow

1. Create a feature branch from `main`
2. Implement changes following the rules in `.claude/rules/`
3. Run tests before committing
4. Open a pull request for review

## Key Commands

```bash
# Install dependencies
pip install -e .

# Run tests
coverage run -m unittest discover
coverage report -m

# Code quality
ruff check . && isort . && black .
```

## Important Notes

- Follow the Python style rules in `.claude/rules/python-style.md` and SQL style rules in `.claude/rules/sql-style.md`
- All API endpoints must comply with `.claude/rules/api-conventions.md`
- Python tests must follow `.claude/rules/python-testing.md`
- SQL/dbt tests must follow `.claude/rules/sql-testing.md`
