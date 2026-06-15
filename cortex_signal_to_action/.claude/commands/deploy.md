# Deploy

Runs the deployment steps for the specified environment.

## Instructions

1. Confirm the target environment (default: `staging`)
2. Run pre-deployment checks: tests, linting, build
3. Execute the deployment steps below for the target environment
4. Verify deployment health after completion
5. Report success or rollback instructions on failure

## Deployment Steps

### Staging
```bash
# 1. Run tests
coverage run -m unittest discover && coverage report -m

# 2. Build
python setup.py sdist bdist_wheel

# 3. Deploy
# (add your staging deploy command here)

# 4. Health check
# (add your health check command here)
```

### Production
```bash
# Production deployment requires explicit confirmation.
# Run staging steps first, then:
# (add your production deploy command here)
```

## Usage

```
/deploy
/deploy staging
/deploy production
```
