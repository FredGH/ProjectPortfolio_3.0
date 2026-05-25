FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY agentic_triage/ ./agentic_triage/

RUN pip install --no-cache-dir -e .

CMD ["python", "-m", "agentic_triage.api"]
