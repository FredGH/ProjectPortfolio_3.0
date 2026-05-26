from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from langgraph.graph import END, StateGraph

from agentic_triage import settings
from agentic_triage.agent.confidence import compute_confidence
from agentic_triage.agent.pre_filter import apply_auto_p4, is_auto_p4
from agentic_triage.agent.query_rewriter import make_query_rewrite_node
from agentic_triage.core.config import DomainConfig
from agentic_triage.core.state import TriageState
from agentic_triage.preprocessing.keyword import (
    KeywordProcessor,
    build_keyword_processor,
    extract_keywords,
)
from agentic_triage.preprocessing.ner import extract_entities
from agentic_triage.preprocessing.normalizer import SymSpell, build_symspell, normalize
from agentic_triage.preprocessing.sanitizer import sanitize
from agentic_triage.retrieval.reranker import rerank
from agentic_triage.scoring.scorer import compute_priority

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Assess prompt
# ---------------------------------------------------------------------------

_ASSESS_SYSTEM = """\
You are a {domain} complaint triage expert. Analyse the complaint and the \
retrieved knowledge-base context below, then score each dimension as an integer.

COMPLAINT:
{complaint}

RETRIEVED CONTEXT:
{context}

SCORING DIMENSIONS:
{dimensions}

Return a JSON object with the exact dimension names as keys, integer scores as \
values, and a "reasoning" key containing a 1–2 sentence explanation.
Return ONLY the JSON — no preamble, no markdown fences.
Example: {{"fraud_risk": 3, "regulatory_breach": 1, "reasoning": "..."}}
"""


def _format_context(retrieved_context: dict[str, list]) -> str:
    parts: list[str] = []
    for role, chunks in retrieved_context.items():
        parts.append(f"[{role.upper()}]")
        for chunk in chunks:
            text = chunk.get("text", str(chunk))
            parts.append(f"  • {text[:400]}")
    return "\n".join(parts) or "(no context retrieved)"


def _format_dimensions(config: DomainConfig) -> str:
    lines: list[str] = []
    for d in config.scoring_dimensions:
        line = f"- {d.name} (0–{d.max_score}): {d.description}"
        if d.high_score_examples:
            line += f"\n  High score: {'; '.join(d.high_score_examples)}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Node factories
# ---------------------------------------------------------------------------


def make_sanitize_node():
    async def _sanitize(state: TriageState) -> dict:
        return {"sanitized_text": sanitize(state["raw_text"])}

    return _sanitize


def make_preprocess_node(
    config: DomainConfig,
    nlp: Any,
    sym_spell: SymSpell | None = None,
    gliner_model: Any | None = None,
    keyword_processor: KeywordProcessor | None = None,
):
    _sym_spell = sym_spell or build_symspell(config.keyword_library_path)
    _kp = keyword_processor or (
        build_keyword_processor(config.keyword_library_path)
        if config.keyword_library_path
        else None
    )

    async def _preprocess(state: TriageState) -> dict:
        text = state["sanitized_text"]
        cleaned = normalize(text, _sym_spell)
        entities = extract_entities(cleaned, config.ner_labels, nlp, gliner_model)
        keywords = extract_keywords(cleaned, _kp) if _kp else []
        return {
            "cleaned_text": cleaned,
            "entities": entities,
            "triggered_keywords": keywords,
        }

    return _preprocess


def make_retrieve_node(config: DomainConfig, retriever: Any):
    """Build the retrieve node.

    retriever must expose a `.search(collection, query, top_k, search_mode)` method
    (i.e. a QdrantRetriever instance).
    """

    async def _retrieve(state: TriageState) -> dict:
        queries: list[str] = state.get("retrieval_queries") or [
            state.get("hyde_text") or state["cleaned_text"]
        ]
        primary_query = queries[0]

        raw: dict[str, list] = {col.name: [] for col in config.collections}
        scores: dict[str, float] = {}

        for query in queries:
            for col in config.collections:
                hits = retriever.search(
                    col.name, query, top_k=col.top_k, search_mode=col.search_mode
                )
                raw[col.name].extend(hits)
                scores[col.name] = max(
                    scores.get(col.name, 0.0),
                    max((h.get("score", 0.0) for h in hits), default=0.0),
                )

        # Deduplicate — keep highest-scoring copy per point ID
        for col_name, chunks in raw.items():
            seen: dict[str, dict] = {}
            for chunk in chunks:
                pid = chunk.get("id")
                if pid not in seen or chunk.get("score", 0) > seen[pid].get("score", 0):
                    seen[pid] = chunk
            raw[col_name] = list(seen.values())

        # Rerank merged results (Guard Rail 11)
        retrieved_context: dict[str, list] = {}
        for col in config.collections:
            chunks = raw[col.name]
            retrieved_context[col.role] = rerank(primary_query, chunks)

        # Compute precedent_scores from complaints_history hits
        precedent_scores = _compute_precedent_scores(config, raw)

        return {
            "retrieved_context": retrieved_context,
            "retrieval_scores": scores,
            "precedent_scores": precedent_scores,
        }

    return _retrieve


def _compute_precedent_scores(
    config: DomainConfig, raw: dict[str, list]
) -> dict[str, float]:
    """Average dimension_scores payload from complaints_history hits."""
    precedent_col = next(
        (col.name for col in config.collections if col.role == "precedent"), None
    )
    if not precedent_col:
        return {}

    dim_totals: dict[str, float] = {}
    dim_counts: dict[str, int] = {}
    for chunk in raw.get(precedent_col, []):
        ds = chunk.get("dimension_scores")
        if not isinstance(ds, dict):
            continue
        for dim, score in ds.items():
            dim_totals[dim] = dim_totals.get(dim, 0.0) + float(score)
            dim_counts[dim] = dim_counts.get(dim, 0) + 1

    return {
        dim: dim_totals[dim] / dim_counts[dim] for dim in dim_totals if dim_counts[dim]
    }


def make_pre_filter_node(config: DomainConfig):
    async def _pre_filter(state: TriageState) -> dict:
        if is_auto_p4(state, config):
            return apply_auto_p4(state, config)
        return {"is_auto_p4": False}

    return _pre_filter


def make_assess_node(config: DomainConfig):
    async def _assess(state: TriageState) -> dict:
        prompt = _ASSESS_SYSTEM.format(
            domain=config.domain_name,
            complaint=state["sanitized_text"],
            context=_format_context(state.get("retrieved_context") or {}),
            dimensions=_format_dimensions(config),
        )

        raw_json = await _call_ollama_json(prompt)
        dimension_scores = _parse_dimension_scores(raw_json, config)
        reasoning = raw_json.get("reasoning", "")

        priority, composite = compute_priority(dimension_scores, config)
        confidence, low_reason = compute_confidence(
            {**state, "dimension_scores": dimension_scores}, config
        )

        # Recommended action from the matched priority level
        recommended_action = next(
            (lv.recommended_action for lv in config.priority_levels if lv.label == priority),
            "",
        )

        return {
            "dimension_scores": dimension_scores,
            "composite_score": composite,
            "priority": priority,
            "confidence": confidence,
            "low_confidence_reason": low_reason,
            "reasoning": reasoning,
            "recommended_action": recommended_action,
        }

    return _assess


def make_finalize_node(config: DomainConfig):  # noqa: ARG001
    async def _finalize(state: TriageState) -> dict:
        # Assemble retrieved_references (role → list of point IDs) for TriageResult
        retrieved_references: dict[str, list[str]] = {
            role: [str(c.get("id", "")) for c in chunks]
            for role, chunks in (state.get("retrieved_context") or {}).items()
        }
        return {"retrieved_references": retrieved_references}

    return _finalize


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def _should_skip_llm(state: TriageState) -> str:
    return "auto_p4" if state.get("is_auto_p4") else "assess"


def _should_reretrieve(config: DomainConfig):
    def _fn(state: TriageState) -> str:
        below_threshold = state["confidence"] < config.confidence_threshold
        under_cap = state["loop_count"] < config.max_reretrieval_loops
        return "reretrieve" if (below_threshold and under_cap) else "done"

    return _fn


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph(
    config: DomainConfig,
    retriever: Any,
    nlp: Any,
    sym_spell: SymSpell | None = None,
    gliner_model: Any | None = None,
    keyword_processor: KeywordProcessor | None = None,
):
    """Compile the full LangGraph triage agent for the given domain config.

    Args:
        config: Domain-specific configuration (scoring, collections, thresholds).
        retriever: A QdrantRetriever instance (or compatible duck type).
        nlp: A loaded spaCy Language object.
        sym_spell: Optional pre-built SymSpell instance; built from config if None.
        gliner_model: Optional GLiNER model for custom NER labels.
        keyword_processor: Optional pre-built FlashText processor; built from config if None.
    """
    graph = StateGraph(TriageState)

    graph.add_node("sanitize", make_sanitize_node())
    graph.add_node(
        "preprocess",
        make_preprocess_node(config, nlp, sym_spell, gliner_model, keyword_processor),
    )

    pre_retrieve = "preprocess"

    if config.use_hyde:
        from agentic_triage.retrieval.hyde import make_hyde_node

        graph.add_node("hyde", make_hyde_node(config))
        graph.add_edge(pre_retrieve, "hyde")
        pre_retrieve = "hyde"

    if config.multi_query_n > 0:
        from agentic_triage.retrieval.multi_query import make_multi_query_node

        graph.add_node("multi_query", make_multi_query_node(config))
        graph.add_edge(pre_retrieve, "multi_query")
        pre_retrieve = "multi_query"

    graph.add_node("retrieve", make_retrieve_node(config, retriever))
    graph.add_node("pre_filter", make_pre_filter_node(config))
    graph.add_node("assess", make_assess_node(config))
    graph.add_node("query_rewrite", make_query_rewrite_node(config))
    graph.add_node("finalize", make_finalize_node(config))

    graph.set_entry_point("sanitize")
    graph.add_edge("sanitize", "preprocess")
    graph.add_edge(pre_retrieve, "retrieve")
    graph.add_edge("retrieve", "pre_filter")
    graph.add_conditional_edges(
        "pre_filter",
        _should_skip_llm,
        {"auto_p4": "finalize", "assess": "assess"},
    )
    graph.add_conditional_edges(
        "assess",
        _should_reretrieve(config),
        {"reretrieve": "query_rewrite", "done": "finalize"},
    )
    graph.add_edge("query_rewrite", "retrieve")
    graph.add_edge("finalize", END)

    return graph.compile()


def build_assess_graph(config: DomainConfig, retriever: Any):
    """Compile the assess sub-graph used by the assess arq worker.

    Entry point is the ``assess`` node; the re-retrieval loop (assess →
    query_rewrite → retrieve → assess) is capped by ``_should_reretrieve``.
    The fast preprocessing nodes (sanitize, preprocess, pre_filter) are not
    included — the worker passes in pre-populated state from the fast queue.
    """
    graph = StateGraph(TriageState)

    graph.add_node("assess", make_assess_node(config))
    graph.add_node("query_rewrite", make_query_rewrite_node(config))
    graph.add_node("retrieve", make_retrieve_node(config, retriever))
    graph.add_node("finalize", make_finalize_node(config))

    graph.set_entry_point("assess")
    graph.add_conditional_edges(
        "assess",
        _should_reretrieve(config),
        {"reretrieve": "query_rewrite", "done": "finalize"},
    )
    graph.add_edge("query_rewrite", "retrieve")
    graph.add_edge("retrieve", "assess")
    graph.add_edge("finalize", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Ollama helpers (shared by assess node)
# ---------------------------------------------------------------------------


async def _call_ollama_json(prompt: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{settings.OLLAMA_HOST}/api/generate",
            json={
                "model": settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
        )
        r.raise_for_status()
    try:
        return json.loads(r.json()["response"])
    except (json.JSONDecodeError, KeyError):
        log.warning("Ollama returned non-JSON assess response")
        return {}


def _parse_dimension_scores(
    raw: dict, config: DomainConfig
) -> dict[str, int]:
    scores: dict[str, int] = {}
    for d in config.scoring_dimensions:
        raw_val = raw.get(d.name, 0)
        try:
            scores[d.name] = max(d.min_score, min(d.max_score, int(raw_val)))
        except (TypeError, ValueError):
            scores[d.name] = d.min_score
    return scores
