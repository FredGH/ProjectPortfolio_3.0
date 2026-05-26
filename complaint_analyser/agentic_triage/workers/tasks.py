from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import spacy
import yaml
from arq import ArqRedis
from arq.connections import RedisSettings
from qdrant_client import QdrantClient

from agentic_triage import settings
from agentic_triage.agent.graph import (
    build_assess_graph,
    make_finalize_node,
    make_pre_filter_node,
    make_preprocess_node,
    make_retrieve_node,
    make_sanitize_node,
)
from agentic_triage.core.config import DomainConfig
from agentic_triage.core.state import TriageState
from agentic_triage.preprocessing.keyword import build_keyword_processor
from agentic_triage.preprocessing.normalizer import build_symspell
from agentic_triage.retrieval.cache import (
    ensure_cache_collection,
    embed_text,
    lookup_cache,
    write_cache,
)
from agentic_triage.retrieval.feedback import (
    write_triage_result,
    write_triage_result_from_cache,
)
from agentic_triage.retrieval.qdrant import QdrantRetriever

log = logging.getLogger(__name__)

_DOMAINS_DIR = Path("domains")


# ---------------------------------------------------------------------------
# Startup — runs once per worker process
# ---------------------------------------------------------------------------


def _make_embed_fn():
    def embed(texts: list[str]) -> list[list[float]]:
        resp = httpx.post(
            f"{settings.OLLAMA_HOST}/api/embed",
            json={"model": settings.EMBED_MODEL, "input": texts},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]

    return embed


async def startup(ctx: dict) -> None:
    configs: dict[str, DomainConfig] = {}
    assess_graphs: dict[str, Any] = {}
    fast_nodes: dict[str, dict[str, Any]] = {}

    qdrant = QdrantClient(url=settings.QDRANT_HOST)
    retriever = QdrantRetriever(client=qdrant, embed_fn=_make_embed_fn())
    nlp = spacy.load("en_core_web_lg")

    ensure_cache_collection(qdrant)

    for config_path in sorted(_DOMAINS_DIR.rglob("config.yaml")):
        domain = config_path.parent.name
        cfg = DomainConfig.from_dict(yaml.safe_load(config_path.read_text()))
        configs[domain] = cfg

        sym_spell = build_symspell(cfg.keyword_library_path)
        kp = (
            build_keyword_processor(cfg.keyword_library_path)
            if cfg.keyword_library_path
            else None
        )

        fast_nodes[domain] = {
            "sanitize": make_sanitize_node(),
            "preprocess": make_preprocess_node(cfg, nlp, sym_spell, keyword_processor=kp),
            "retrieve": make_retrieve_node(cfg, retriever),
            "pre_filter": make_pre_filter_node(cfg),
            "finalize": make_finalize_node(cfg),
        }
        assess_graphs[domain] = build_assess_graph(cfg, retriever)
        log.info("Loaded domain: %s", domain)

    ctx["configs"] = configs
    ctx["assess_graphs"] = assess_graphs
    ctx["fast_nodes"] = fast_nodes
    ctx["qdrant"] = qdrant


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


async def fast_preprocess_task(
    ctx: dict,
    item_id: str,
    batch_id: str,
    domain: str,
    raw_text: str,
) -> None:
    """Sanitize → (cache check) → preprocess → retrieve → pre_filter. No Ollama call."""
    nodes = ctx["fast_nodes"][domain]
    redis: ArqRedis = ctx["redis"]
    qdrant: QdrantClient = ctx["qdrant"]

    state: TriageState = _init_state(item_id, batch_id, raw_text)

    # 1. Sanitize — cheap, needed before embedding
    state = {**state, **await nodes["sanitize"](state)}

    # 2. Semantic cache lookup — short-circuits everything on a hit
    embedding = await embed_text(state["sanitized_text"])
    cached = lookup_cache(qdrant, embedding, domain)
    if cached:
        await write_triage_result_from_cache(cached, item_id, batch_id, domain)
        await _increment_batch_counter(batch_id, outcome="done")
        return

    # 3. Preprocessing pipeline
    state = {**state, **await nodes["preprocess"](state)}
    state = {**state, **await nodes["retrieve"](state)}
    state = {**state, **await nodes["pre_filter"](state)}

    if state["is_auto_p4"]:
        state = {**state, **await nodes["finalize"](state)}
        await write_triage_result(state, domain, auto=True)
        write_cache(qdrant, embedding, _result_from_state(state), domain)
        await _increment_batch_counter(batch_id, outcome="done")
        return

    # 4. Pass state + embedding to assess worker (cache write happens there)
    await redis.enqueue_job(
        "assess_task",
        item_id,
        batch_id,
        domain,
        dict(state),
        embedding,
        _queue_name="assess",
    )


async def assess_task(
    ctx: dict,
    item_id: str,  # noqa: ARG001 — retained for step 12 idempotency check
    batch_id: str,
    domain: str,
    state: dict,
    embedding: list[float],
) -> None:
    """Assess → (query_rewrite → retrieve →)* finalize. Calls Ollama."""
    graph = ctx["assess_graphs"][domain]
    qdrant: QdrantClient = ctx["qdrant"]
    try:
        result_state: TriageState = await graph.ainvoke(state)
        await write_triage_result(result_state, domain, auto=False)
        write_cache(qdrant, embedding, _result_from_state(result_state), domain)
        await _increment_batch_counter(batch_id, outcome="done")
    except Exception:
        log.exception("assess_task failed item=%s batch=%s", item_id, batch_id)
        await _increment_batch_counter(batch_id, outcome="failed")
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _increment_batch_counter(batch_id: str, outcome: str) -> None:  # noqa: ARG001
    # Stub — real Postgres UPDATE implemented in step 12
    pass


def _result_from_state(state: TriageState):
    from agentic_triage.retrieval.feedback import build_triage_result

    return build_triage_result(state)


def _init_state(item_id: str, batch_id: str, raw_text: str) -> TriageState:
    return {
        "input_id": item_id,
        "batch_id": batch_id,
        "raw_text": raw_text,
        "sanitized_text": "",
        "cleaned_text": "",
        "entities": {},
        "triggered_keywords": [],
        "retrieved_context": {},
        "retrieval_scores": {},
        "dimension_scores": {},
        "precedent_scores": {},
        "composite_score": 0.0,
        "is_auto_p4": False,
        "priority": "",
        "confidence": 0.0,
        "low_confidence_reason": None,
        "loop_count": 0,
        "reasoning": "",
        "recommended_action": "",
        "analyst_override": None,
        "hyde_text": None,
        "retrieval_queries": [],
        "retrieved_references": {},
    }


# ---------------------------------------------------------------------------
# Worker settings
# ---------------------------------------------------------------------------


class FastWorkerSettings:
    functions = [fast_preprocess_task]
    on_startup = startup
    max_jobs = 3
    queue_name = "fast"
    redis_settings = RedisSettings(host="redis", port=6379)


class AssessWorkerSettings:
    functions = [assess_task]
    on_startup = startup
    max_jobs = 2
    queue_name = "assess"
    redis_settings = RedisSettings(host="redis", port=6379)
