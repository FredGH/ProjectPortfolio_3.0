FROM python:3.11-slim

WORKDIR /app

# Install main package first (cached layer — invalidated only on dep changes)
COPY pyproject.toml ./
COPY agentic_triage/ ./agentic_triage/
RUN pip install --no-cache-dir -e .

# Install service-specific deps
COPY spacy_service/requirements.txt ./spacy_service/requirements.txt
RUN pip install --no-cache-dir -r spacy_service/requirements.txt

# Download model as a separate cached layer
RUN python -m spacy download en_core_web_lg

COPY spacy_service/ ./spacy_service/

CMD ["uvicorn", "spacy_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
