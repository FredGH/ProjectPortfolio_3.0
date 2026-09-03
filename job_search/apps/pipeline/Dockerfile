FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/core /app/packages/core
COPY config /app/config
COPY apps/pipeline/app /app/apps/pipeline/app

ENV PYTHONPATH=/app/packages/core:/app/apps/pipeline

ENTRYPOINT ["python", "-m", "app.cli"]
