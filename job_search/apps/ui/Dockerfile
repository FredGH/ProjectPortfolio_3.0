# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# Step 15's torch/sentence-transformers/docling wheels are large enough
# that pip's default read timeout (15s per chunk) intermittently trips on
# a slow or congested connection, failing the whole build — bump it and
# add retries. The --mount=type=cache on pip's own download cache matters
# even more on a flaky connection: without it, a failed RUN layer discards
# everything already downloaded, so every retry re-fetches the entire
# multi-GB tree from scratch; with it, a retry only has to re-fetch
# whatever actually failed last time (verified live: repeated builds on
# this exact connection were re-downloading gigabytes on every retry).
ENV PIP_DEFAULT_TIMEOUT=180
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 5 -r requirements.txt

COPY packages/core /app/packages/core
COPY config /app/config
COPY apps/ui/app /app/apps/ui/app
COPY apps/ui/.streamlit /app/.streamlit

ENV PYTHONPATH=/app/packages/core

EXPOSE 8501

CMD ["streamlit", "run", "/app/apps/ui/app/Home.py", "--server.address=0.0.0.0", "--server.port=8501"]
