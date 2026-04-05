# Fix Issue

Takes an issue description or issue number and applies a fix.

## Instructions

1. Read the issue details (from argument or GitHub if an issue number is given)
2. Locate the relevant files in the codebase
3. Understand the root cause before making changes
4. Apply the minimal fix needed — do not refactor surrounding code
5. Add or update tests to cover the fix
6. Summarize what was changed and why

## Steps

1. **Understand** — re-read the issue, identify affected files
2. **Diagnose** — find the root cause, not just the symptom
3. **Fix** — apply the smallest correct change
4. **Test** — ensure existing tests pass; add a regression test
5. **Report** — list changed files and a one-line explanation per change

## Usage

```
/fix-issue Login fails when email contains uppercase letters
/fix-issue #42
```
