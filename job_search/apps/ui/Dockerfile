FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/core /app/packages/core
COPY apps/ui/app /app/apps/ui/app

ENV PYTHONPATH=/app/packages/core

EXPOSE 8501

CMD ["streamlit", "run", "/app/apps/ui/app/Home.py", "--server.address=0.0.0.0", "--server.port=8501"]
