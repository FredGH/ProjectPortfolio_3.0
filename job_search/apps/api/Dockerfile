# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# docling's table-structure model imports opencv-python (cv2), which needs
# these shared libraries present on the system even though pip installs
# opencv's own Python wheel fine — python:3.11-slim doesn't ship them.
# Without this, CV extraction fails at first use with
# "ImportError: libxcb.so.1: cannot open shared object file" (JOB-202).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxcb1 \
    && rm -rf /var/lib/apt/lists/*

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
COPY prompts /app/prompts
COPY apps/api/app /app/apps/api/app

ENV PYTHONPATH=/app/packages/core:/app/apps/api

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/apps/api"]
