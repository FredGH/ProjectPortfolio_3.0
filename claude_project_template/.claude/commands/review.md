# Code Review

Perform a thorough code review of the current changes or the specified file/PR.

## Instructions

1. Read all changed files using the git diff or the files provided
2. For `.py` files: check against `.claude/rules/python-style.md` and `.claude/rules/python-testing.md`
3. For `.sql` files: check against `.claude/rules/sql-style.md` and `.claude/rules/sql-testing.md`
4. For API endpoint files: check against `.claude/commands/api-review.md`
5. Report findings grouped by severity: **Critical**, **Warning**, **Suggestion**

## Output Format

```
## Code Review

### Critical
- [file:line] Issue description

### Warnings
- [file:line] Issue description

### Suggestions
- [file:line] Improvement idea

### Summary
Overall assessment in 2-3 sentences.
```

## Usage

```
/review
/review src/api/routes.py
/review PR #123
```
