# Security Review Skill

Orchestrates a security audit by routing to the appropriate specialized agent based on file type.

## Trigger Conditions

Auto-invoked when changes touch:
- Authentication or authorization logic
- Input validation or sanitization
- Database queries or dbt models
- File I/O or path handling
- Cryptography or secret handling
- External API integrations
- Dependency updates (`requirements.txt`, `packages.yml`)

## Workflow

### Step 1 — Classify files in scope
Identify all changed or specified files and group them:
- `.py` files → route to `python-security-auditor` agent
- `.sql`, `.yml` (dbt models/sources), `schema.yml` → route to `sql-security-auditor` agent
- Other files (shell scripts, config, CI/CD) → run generic OWASP pass inline (Step 3)

### Step 2 — Delegate to specialized agents

**For Python files:**
> Use the `python-security-auditor` agent. Pass the list of `.py` files in scope.

**For SQL/dbt files:**
> Use the `sql-security-auditor` agent. Pass the list of `.sql` and dbt `.yml` files in scope.

### Step 3 — Generic OWASP pass (non-Python, non-SQL files)
For any remaining files not handled by a specialized agent, check:
- [ ] Hardcoded secrets or credentials
- [ ] `shell=True` or equivalent in shell/CI scripts
- [ ] Overly permissive file or network permissions
- [ ] Sensitive data in logs or environment variable definitions
- [ ] Dependency pinning in lockfiles

### Step 4 — Consolidate and report
Merge findings from all agents into a single report:

```
## Security Review Summary

### Scope
- Python files reviewed: <list>
- SQL/dbt files reviewed: <list>
- Other files reviewed: <list>

### Critical Findings
- [agent] [file:line] <issue>

### High Findings
- [agent] [file:line] <issue>

### Medium / Low / Info
- [agent] [file:line] <issue>

### Overall Risk: CRITICAL / HIGH / MEDIUM / LOW
One paragraph summary. Blockers called out explicitly.
```

## Usage

```
/security-review
/security-review src/auth/
/security-review models/fct_orders.sql
```
