# Docker Deploy Skill

Builds a Docker image for a Python project and deploys it to Hugging Face Spaces or AWS ECS/Fargate.

## Trigger Conditions

Invoked when the user requests:
- A Docker build and deploy
- Deployment to Hugging Face Spaces
- Deployment to AWS ECS or Fargate
- Containerisation of a Python project

## Workflow

### Step 1 — Pre-flight checks

- [ ] All tests pass: `coverage run -m unittest discover`
- [ ] No linting errors: `ruff check .`
- [ ] No formatting issues: `black --check .`
- [ ] No uncommitted changes: `git status`
- [ ] `Dockerfile` exists — if not, generate one (see Step 2)
- [ ] `.dockerignore` exists — if not, generate one (see Step 2)
- [ ] No secrets hardcoded in source files (run `git grep -i "password\|secret\|token\|api_key"`)

---

### Step 2 — Generate Dockerfile (if missing)

Generate a production-ready `Dockerfile` for a Python 3.11 project:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install the package in editable mode if setup.py exists
RUN if [ -f setup.py ]; then pip install --no-cache-dir -e .; fi

# Non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 7860

CMD ["python", "app.py"]
```

Generate `.dockerignore`:
```
.git
.gitignore
.env
.env.*
venv/
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.coverage
htmlcov/
.pytest_cache/
*.md
```

---

### Step 3 — Build and test image locally

```bash
# Build
docker build -t <image-name>:latest .

# Verify image builds without errors
docker images | grep <image-name>

# Run locally and smoke test
docker run --rm -p 7860:7860 --env-file .env <image-name>:latest
```

Verify:
- [ ] Container starts without errors
- [ ] App responds on expected port
- [ ] No secrets baked into the image: `docker history <image-name>:latest`

---

### Step 4 — Confirm deploy target

**Before proceeding, ask the user:**

> Where would you like to deploy?
>
> 1. **Hugging Face Spaces** — public demo, simple Docker push, free tier available
> 2. **AWS ECS / Fargate** — production-grade, scalable, requires AWS account and ECS cluster
>
> Reply with `1`, `2`, `huggingface`, or `ecs`.

Wait for the user's reply before continuing. Do not assume a default target.

---

#### Target A: Hugging Face Spaces

**Prerequisites:**
- `huggingface_hub` installed: `pip install huggingface_hub`
- Logged in: `huggingface-cli login`
- Space created at huggingface.co (Docker SDK type)

**Deploy:**
```bash
# Tag image for HF registry
docker tag <image-name>:latest registry.hf.space/<hf-username>/<space-name>:latest

# Login to HF registry
huggingface-cli login
docker login registry.hf.space -u <hf-username> -p $(huggingface-cli whoami --token)

# Push
docker push registry.hf.space/<hf-username>/<space-name>:latest
```

**Environment variables** — set in the Space settings UI or via API:
```bash
python3 -c "
from huggingface_hub import HfApi
api = HfApi()
api.add_space_secret('<hf-username>/<space-name>', 'MY_SECRET', 'value')
"
```

**Verify:**
- [ ] Space rebuilds and shows "Running" status
- [ ] App is reachable at `https://<hf-username>-<space-name>.hf.space`

---

#### Target B: AWS ECS / Fargate

**Prerequisites:**
- AWS CLI configured: `aws sts get-caller-identity`
- ECR repository exists (or create one)
- ECS cluster and task definition exist (or create them)

**Step B1 — Push image to ECR:**
```bash
# Get ECR login token
aws ecr get-login-password --region <region> | \
  docker login --username AWS --password-stdin \
  <account-id>.dkr.ecr.<region>.amazonaws.com

# Tag for ECR
docker tag <image-name>:latest \
  <account-id>.dkr.ecr.<region>.amazonaws.com/<ecr-repo>:latest

# Push
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/<ecr-repo>:latest
```

**Step B2 — Update ECS service:**
```bash
# Register new task definition revision with updated image URI
aws ecs register-task-definition \
  --cli-input-json file://task-definition.json

# Update service to use latest task definition
aws ecs update-service \
  --cluster <cluster-name> \
  --service <service-name> \
  --force-new-deployment

# Wait for deployment to stabilise
aws ecs wait services-stable \
  --cluster <cluster-name> \
  --services <service-name>
```

**Step B3 — Verify:**
```bash
# Check running tasks
aws ecs list-tasks --cluster <cluster-name> --service-name <service-name>

# Check service events for errors
aws ecs describe-services \
  --cluster <cluster-name> \
  --services <service-name> \
  --query 'services[0].events[:5]'
```

- [ ] New task is in RUNNING state
- [ ] Old task has been stopped
- [ ] Load balancer health check passes (if applicable)

---

### Step 5 — Post-deploy verification

- [ ] Application responds on the public URL
- [ ] No error spike in CloudWatch / HF Space logs
- [ ] Key smoke test passes (hit the main endpoint)

---

### Rollback Procedure

**Hugging Face:** push the previous image tag to the registry:
```bash
docker tag <image-name>:<previous-tag> registry.hf.space/<hf-username>/<space-name>:latest
docker push registry.hf.space/<hf-username>/<space-name>:latest
```

**AWS ECS:** redeploy the previous task definition revision:
```bash
aws ecs update-service \
  --cluster <cluster-name> \
  --service <service-name> \
  --task-definition <task-def-name>:<previous-revision>
```

---

## Usage

```
/docker-deploy                # target will be prompted interactively
/docker-deploy huggingface    # skip prompt, deploy to Hugging Face Spaces
/docker-deploy ecs            # skip prompt, deploy to AWS ECS/Fargate
```
