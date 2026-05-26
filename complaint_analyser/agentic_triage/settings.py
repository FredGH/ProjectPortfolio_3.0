from __future__ import annotations

import os

OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "nomic-embed-text")
QDRANT_HOST: str = os.getenv("QDRANT_HOST", "http://localhost:6333")
REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://localhost/triage")
