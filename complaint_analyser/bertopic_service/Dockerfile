FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    bertopic>=0.16 \
    fastapi>=0.111 \
    uvicorn[standard]>=0.29 \
    asyncpg>=0.29

COPY bertopic_service/ ./bertopic_service/

CMD ["python", "-m", "bertopic_service.main"]
