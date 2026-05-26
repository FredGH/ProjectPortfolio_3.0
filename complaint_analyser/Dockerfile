FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY agentic_triage/ ./agentic_triage/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY domains/ ./domains/
COPY sql/ ./sql/

RUN pip install --no-cache-dir -e .

CMD ["python", "scripts/run_api.py"]
