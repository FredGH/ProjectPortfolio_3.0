FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# Step 15's torch/sentence-transformers wheels are large enough that pip's
# default read timeout (15s per chunk) intermittently trips on a slow or
# congested connection, failing the whole build — bump it and add retries
# rather than depend on the network being fast every time.
ENV PIP_DEFAULT_TIMEOUT=180
RUN pip install --no-cache-dir --retries 5 -r requirements.txt

COPY packages/core /app/packages/core
COPY config /app/config
COPY apps/ui/app /app/apps/ui/app

ENV PYTHONPATH=/app/packages/core

EXPOSE 8501

CMD ["streamlit", "run", "/app/apps/ui/app/Home.py", "--server.address=0.0.0.0", "--server.port=8501"]
