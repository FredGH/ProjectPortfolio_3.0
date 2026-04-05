# Deploy Skill

Structured deployment checklist workflow — auto-invoked when a deploy is requested.

## Pre-Deployment Gates

All gates must pass before proceeding:

- [ ] All tests pass: `coverage run -m unittest discover`
- [ ] No linting errors: `ruff check .`
- [ ] No formatting issues: `black --check .`
- [ ] Branch is up to date with `main`
- [ ] No uncommitted changes

## Deployment Stages

### 1. Build
```bash
python setup.py sdist bdist_wheel
```
Verify: build artifacts exist in `dist/`

### 2. Stage
Deploy to the staging environment and run smoke tests.

Verify:
- [ ] App starts without errors
- [ ] Health check endpoint returns 200
- [ ] Key user flows work (smoke test)

### 3. Promote to Production
Only after staging verification:
- [ ] Staging has been running cleanly for at least 10 minutes
- [ ] No spike in error rate on staging
- [ ] Stakeholder sign-off (if required)

### 4. Post-Deploy Verification
- [ ] Health check in production returns 200
- [ ] Error rate is within baseline
- [ ] Key metrics are normal

## Rollback Procedure

If any post-deploy check fails:
```bash
# Revert to previous version
# (add your rollback command here)
```

Document the incident and root cause before re-deploying.
