"""
KB recall evaluation — verifies that each Qdrant collection returns relevant
results for domain test queries.

Usage:
  python evaluation/eval_kb.py
  python evaluation/eval_kb.py --top-k 5 --threshold 0.70
  python evaluation/eval_kb.py --qdrant-url http://localhost:6333 --ollama-url http://localhost:11434

Recall is measured via payload-match: a hit is counted when any field in the
returned chunk's payload contains at least one of the expected_payload_matches
strings (case-insensitive). This avoids the need to pre-record Qdrant point
IDs, which change on each recreate run.

Exit codes:
  0 — all collections pass recall@k >= threshold
  1 — one or more collections fail or Qdrant is unreachable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import httpx
from qdrant_client import QdrantClient

EMBED_MODEL = "nomic-embed-text"


def embed(texts: list[str], ollama_url: str) -> list[list[float]]:
    resp = httpx.post(
        f"{ollama_url}/api/embed",
        json={"model": EMBED_MODEL, "input": texts},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def search(
    client: QdrantClient,
    collection: str,
    embedding: list[float],
    top_k: int,
) -> list[dict]:
    result = client.query_points(
        collection_name=collection,
        query=embedding,
        limit=top_k,
        with_payload=True,
        with_vectors=False,
    )
    return [{"id": str(h.id), "score": h.score, **h.payload} for h in result.points]


def _payload_contains_match(hit: dict, expected_matches: list[str]) -> bool:
    # Include both keys and values so checks like ["priority"] match the field name
    parts = [str(x) for kv in hit.items() for x in kv]
    payload_text = " ".join(parts).lower()
    return any(m.lower() in payload_text for m in expected_matches)


def evaluate(
    client: QdrantClient,
    queries: list[dict],
    ollama_url: str,
    top_k: int,
) -> dict[str, dict]:
    stats: dict[str, dict] = defaultdict(
        lambda: {"hits": 0, "total": 0, "failures": []}
    )

    for entry in queries:
        collection = entry["collection"]
        query = entry["query"]
        expected = entry.get("expected_payload_matches", [])

        try:
            existing = {c.name for c in client.get_collections().collections}
            if collection not in existing:
                print(
                    f"  SKIP {collection!r} — collection not found (run ingest first)"
                )
                continue

            [embedding] = embed([query], ollama_url)
            hits = search(client, collection, embedding, top_k)

            matched = any(_payload_contains_match(h, expected) for h in hits)
            stats[collection]["total"] += 1
            if matched:
                stats[collection]["hits"] += 1
            else:
                stats[collection]["failures"].append(query)

        except Exception as exc:
            print(f"  ERROR {collection!r} query={query!r}: {exc}")
            stats[collection]["total"] += 1
            stats[collection]["failures"].append(query)

    return dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="KB recall@k evaluation.")
    parser.add_argument("--queries", default="evaluation/kb_test_queries.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=0.70)
    parser.add_argument(
        "--qdrant-url", default=os.environ.get("QDRANT_HOST", "http://localhost:6333")
    )
    parser.add_argument(
        "--ollama-url", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    args = parser.parse_args()

    queries = json.loads(Path(args.queries).read_text())

    try:
        client = QdrantClient(url=args.qdrant_url)
        client.get_collections()
    except Exception as exc:
        print(f"Cannot connect to Qdrant at {args.qdrant_url}: {exc}")
        sys.exit(1)

    print(f"Evaluating recall@{args.top_k} (threshold={args.threshold})\n")
    stats = evaluate(client, queries, args.ollama_url, args.top_k)

    passed = True
    for collection, s in sorted(stats.items()):
        total = s["total"]
        if total == 0:
            print(f"  {collection}: SKIPPED (no queries ran)")
            continue
        recall = s["hits"] / total
        status = "PASS" if recall >= args.threshold else "FAIL"
        print(
            f"  {collection}: recall@{args.top_k}={recall:.2f} ({s['hits']}/{total}) [{status}]"
        )
        if s["failures"]:
            for q in s["failures"]:
                print(f"    miss: {q}")
        if recall < args.threshold:
            passed = False

    if not stats:
        print("No collections evaluated — run ingest first.")
        sys.exit(0)

    print()
    if passed:
        print("All collections pass.")
    else:
        print("One or more collections failed recall threshold.")
        sys.exit(1)


if __name__ == "__main__":
    main()
