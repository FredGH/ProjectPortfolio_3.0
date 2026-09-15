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
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/core /app/packages/core
COPY config /app/config
COPY prompts /app/prompts
COPY apps/api/app /app/apps/api/app

ENV PYTHONPATH=/app/packages/core:/app/apps/api

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/apps/api"]
