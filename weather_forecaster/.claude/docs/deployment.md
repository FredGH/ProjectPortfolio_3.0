# Deployment Guide

This guide covers deploying the data engineering pipeline using Docker, environment configuration, and CI/CD integration.

## Table of Contents
- [Docker Setup](#docker-setup)
- [Environment Configuration](#environment-configuration)
- [Local Development](#local-development)
- [CI/CD Pipeline](#cicd-pipeline)
- [Production Deployment](#production-deployment)
- [Monitoring](#monitoring)

---

## Docker Setup

### Prerequisites

- Docker 20.10+
- Docker Compose 2.0+

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Default command
CMD ["python", "-m", "dlt", "pipeline", "run", "pipeline.py"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  # Main pipeline runner
  pipeline:
    build: .
    volumes:
      - ./data:/app/data
      - ./credentials:/app/credentials:ro
    environment:
      - DLT_DESTINATION=duckdb
      - DLT_DATASET_NAME=bronze
    secrets:
      - api_credentials

  # PostgreSQL destination (optional)
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: de_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: datawarehouse
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  # DuckDB (for local development)
  duckdb:
    image: duckdb/duckdb:latest
    volumes:
      - ./data:/data
    stdin_open: true
    tty: true

volumes:
  postgres_data:

secrets:
  api_credentials:
    file: ./credentials/api_secrets.json
```

### Build and Run

```bash
# Build the image
docker build -t de-pipeline:latest .

# Run with docker-compose
docker-compose up -d

# View logs
docker-compose logs -f pipeline

# Stop
docker-compose down
```

---

## Environment Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `DLT_DESTINATION` | Yes | Destination type | `duckdb` |
| `DLT_DATASET_NAME` | Yes | Dataset/schema name | `bronze` |
| `DLT_PIPELINE_NAME` | No | Pipeline name | Auto-generated |
| `GITHUB_TOKEN` | No | GitHub API token | - |
| `SLACK_WEBHOOK_URL` | No | Slack webhook | - |
| `JIRA_API_KEY` | No | Jira API key | - |
| `GOOGLE_APPLICATION_CREDENTIALS` | No | GCP credentials path | - |

### .env File

Create a `.env` file in the project root:

```bash
# Destination
DLT_DESTINATION=duckdb
DLT_DATASET_NAME=bronze

# API Credentials
GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx
JIRA_API_KEY=xxxxxxxxxxxxx

# Database (if using PostgreSQL)
POSTGRES_PASSWORD=secret_password
```

### Secrets Management

#### Docker Secrets

```yaml
secrets:
  github_token:
    external: true
  slack_webhook:
    file: ./secrets/slack.json
```

#### HashiCorp Vault

```python
import hvac

client = hvac.Client()
secret = client.secrets.kv.v2.read_secret(path="de-pipeline/api-keys")
```

---

## Local Development

### Quick Start

```bash
# Clone and setup
git clone https://github.com/yourorg/data-pipeline.git
cd data-pipeline

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Run tests
pytest tests/ -v

# Run pipeline
python pipelines/main.py
```

### Running with Docker (Development)

```bash
# Mount source code for live reloading
docker-compose -f docker-compose.dev.yml up

# With hot reload
docker-compose -f docker-compose.dev.yml up --build
```

---

## CI/CD Pipeline

### GitHub Actions

Create `.github/workflows/ci.yml`:

```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  DLT_DESTINATION: duckdb
  DLT_DATASET_NAME: test_${{ github.run_id }}

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov
      
      - name: Run tests
        run: |
          pytest tests/ -v --cov=etl_sources --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3

  deploy:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Build Docker image
        run: |
          docker build -t de-pipeline:${{ github.sha }} .
      
      - name: Deploy to production
        run: |
          echo "Deploying to production..."
          # Add your deployment commands
```

### GitLab CI

Create `.gitlab-ci.yml`:

```yaml
stages:
  - test
  - build
  - deploy

test:
  stage: test
  image: python:3.11
  before_script:
    - pip install -r requirements.txt
  script:
    - pytest tests/ -v

build:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  script:
    - docker build -t de-pipeline:$CI_COMMIT_SHA .

deploy:
  stage: deploy
  image: ubuntu:22.04
  script:
    - echo "Deploying..."
  only:
    - main
```

---

## Production Deployment

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: de-pipeline
spec:
  replicas: 1
  selector:
    matchLabels:
      app: de-pipeline
  template:
    metadata:
      labels:
        app: de-pipeline
    spec:
      containers:
      - name: pipeline
        image: de-pipeline:latest
        env:
        - name: DLT_DESTINATION
          value: "bigquery"
        - name: DLT_DATASET_NAME
          value: "production"
        volumeMounts:
        - name: data
          mountPath: /app/data
        resources:
          limits:
            memory: "2Gi"
            cpu: "1000m"
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: de-pipeline-data
```

### Scheduled Execution

Using Cron:

```yaml
# kubernetes-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: de-pipeline-cron
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: pipeline
            image: de-pipeline:latest
          restartPolicy: OnFailure
```

---

## Monitoring

### Logging

```python
import logging
import dlt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pipeline = dlt.pipeline(
    pipeline_name="my_pipeline",
    destination="duckdb"
)

try:
    load_info = pipeline.run(source)
    logger.info(f"Loaded {load_info.load_packages} packages")
except Exception as e:
    logger.error(f"Pipeline failed: {e}")
    # Send alert
```

### Alerts

#### Slack Notification

```python
import requests

def send_slack_alert(message: str, webhook_url: str):
    requests.post(webhook_url, json={"text": message})
```

#### Health Check Endpoint

```python
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Connection timeout | Check network/firewall rules |
| Authentication failed | Verify API keys and tokens |
| Out of memory | Increase container memory limit |
| Schema mismatch | Check source data format |

### Debug Mode

```bash
# Enable verbose logging
export DLT_VERBOSE=true
python -m dlt pipeline run pipeline.py --verbose

# Debug specific source
PYTHONPATH=. python -c "
from weather_forecaster_sources.config import get_api_key
from weather_forecaster_sources.weather_source import current_weather
api_key = get_api_key('OPENWEATHER_API_KEY', required=True)
source = current_weather(api_key=api_key, lat=51.5074, lon=-0.1278)
print(list(source))
"
```
