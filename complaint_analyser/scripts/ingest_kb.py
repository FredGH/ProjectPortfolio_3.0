"""
Knowledge-base ingest script — populates the three Qdrant collections.

Usage:
  python scripts/ingest_kb.py --collection regulatory_rules   --source ./data/regulatory/
  python scripts/ingest_kb.py --collection risk_taxonomy      --source ./data/taxonomy.yaml
  python scripts/ingest_kb.py --collection complaints_history --source ./data/complaints_labelled.jsonl
  python scripts/ingest_kb.py --collection regulatory_rules   --source ./data/regulatory/ --recreate

complaints_history reads from the JSONL produced by scripts/bootstrap_labels.py.
Document collections (regulatory_rules, risk_taxonomy) use random UUIDs — pass
--recreate to drop and repopulate when source files change.
complaints_history uses input_id as the Qdrant point ID so re-runs are idempotent.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

import httpx
import yaml
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
QDRANT_HOST = os.environ.get("QDRANT_HOST", "http://localhost:6333")
EMBED_MODEL = "nomic-embed-text"
VECTOR_DIM = 768
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
BATCH_SIZE = 64


def embed(texts: list[str]) -> list[list[float]]:
    resp = httpx.post(
        f"{OLLAMA_HOST}/api/embed",
        json={"model": EMBED_MODEL, "input": texts},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def ensure_collection(client: QdrantClient, name: str, recreate: bool = False) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if recreate and name in existing:
        client.delete_collection(name)
        existing.discard(name)
    if name not in existing:
        client.create_collection(
            name,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )


def chunk_document(text: str) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " "],
    )
    return splitter.split_text(text)


def _upsert(
    client: QdrantClient,
    collection: str,
    records: list[dict],
    id_key: str | None = None,
) -> None:
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        texts = [r["text"] for r in batch]
        vectors = embed(texts)
        client.upsert(
            collection,
            points=[
                PointStruct(
                    id=(
                        str(uuid.uuid5(uuid.NAMESPACE_OID, str(r[id_key])))
                        if id_key
                        else str(uuid.uuid4())
                    ),
                    vector=v,
                    payload={k: val for k, val in r.items()},
                )
                for v, r in zip(vectors, batch)
            ],
        )
        print(f"  upserted {i + len(batch)}/{len(records)}")


def ingest_regulatory(
    client: QdrantClient, collection: str, source_dir: Path, recreate: bool
) -> None:
    ensure_collection(client, collection, recreate)
    records = []
    for path in sorted(source_dir.rglob("*.txt")):
        for chunk in chunk_document(path.read_text()):
            records.append({"text": chunk, "source": path.name, "type": "rule"})
    if not records:
        print(f"  warning: no .txt files found in {source_dir}")
        return
    _upsert(client, collection, records)


def ingest_taxonomy(
    client: QdrantClient, collection: str, source_file: Path, recreate: bool
) -> None:
    ensure_collection(client, collection, recreate)
    entries = yaml.safe_load(source_file.read_text())
    records = [
        {"text": e["definition"], "label": e["label"], "type": "rubric"}
        for e in entries
    ]
    _upsert(client, collection, records)


def ingest_complaints(
    client: QdrantClient, collection: str, source_file: Path, recreate: bool
) -> None:
    """Ingest from complaints_labelled.jsonl produced by bootstrap_labels.py."""
    ensure_collection(client, collection, recreate)
    records = []
    with source_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            records.append(
                {
                    "id": row["input_id"],
                    "text": row.get("narrative", row.get("complaint_text", "")),
                    "priority": row.get("priority", ""),
                    "dimension_scores": row.get("dimension_scores", {}),
                    "composite_score": float(row.get("composite_score", 0)),
                    "confidence": float(row.get("confidence", 0)),
                    "domain": row.get("domain", ""),
                    "source": row.get("source", "bootstrap"),
                    "was_overridden": False,
                }
            )
    if not records:
        print(f"  warning: no records found in {source_file}")
        return
    _upsert(client, collection, records, id_key="id")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Populate Qdrant KB collections.")
    parser.add_argument(
        "--collection",
        required=True,
        choices=["regulatory_rules", "risk_taxonomy", "complaints_history"],
    )
    parser.add_argument("--source", required=True)
    parser.add_argument(
        "--recreate", action="store_true", help="Drop and repopulate the collection"
    )
    parser.add_argument("--qdrant-url", default=QDRANT_HOST)
    parser.add_argument("--ollama-url", default=OLLAMA_HOST)
    args = parser.parse_args()

    OLLAMA_HOST = args.ollama_url

    client = QdrantClient(url=args.qdrant_url)
    src = Path(args.source)

    if args.collection == "regulatory_rules":
        ingest_regulatory(client, args.collection, src, args.recreate)
    elif args.collection == "risk_taxonomy":
        ingest_taxonomy(client, args.collection, src, args.recreate)
    elif args.collection == "complaints_history":
        ingest_complaints(client, args.collection, src, args.recreate)

    print("Done.")
