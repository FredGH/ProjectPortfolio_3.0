"""Cascading job classification (PLAN.md Step 11a): rules -> embedding
-> LLM, cheapest and most-deterministic signal first. Each stage only
runs for a title the previous stage declined to classify — the whole
point of ordering it this way is that the expensive stages (an
embedding call, an LLM call) never run for the majority of titles the
free rules stage already resolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from core.classification.embeddings import classify_by_embedding
from core.classification.llm_classifier import classify_by_llm
from core.classification.rules import classify_by_rules
from core.classification.seniority import derive_seniority_band
from core.llm.types import LLMAdapter

_DEFAULT_CATEGORY_MAP_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "category_map.yml"
)


@dataclass(frozen=True)
class Classification:
    """One job's resolved classification.

    Attributes:
        category: One of the 7-value classification taxonomy.
        category_confidence: Confidence in `category`, 0.0-1.0.
        category_method: Which stage produced `category` — 'rules',
            'embedding', or 'llm'.
        qa_category: The mapped 4-value question-bank category, or
            `None` when `category` has no reasonable question-bank
            equivalent (currently only 'other').
        seniority_band: One of the 5-value seniority taxonomy.
    """

    category: str
    category_confidence: float
    category_method: str
    qa_category: str | None
    seniority_band: str


def load_qa_category_map(path: Path | None = None) -> dict[str, str | None]:
    """Load the classification-category -> qa_category mapping.

    Args:
        path: Path to the mapping YAML. Defaults to
            config/category_map.yml at the repository root.

    Returns:
        classification category -> qa_category (or `None`).
    """
    raw = yaml.safe_load((path or _DEFAULT_CATEGORY_MAP_PATH).read_text())
    return raw["qa_category_map"]


def classify_title(
    title: str | None,
    *,
    centroids: dict[str, list[float]],
    qa_category_map: dict[str, str | None],
    adapters: dict[str, LLMAdapter],
    embedding_base_url: str,
    embedding_model: str,
    http_client: httpx.Client,
) -> Classification:
    """Classify one job title via the rules -> embedding -> LLM cascade.

    Args:
        title: The job title to classify (title_for_display preferred
            — see core.classification.seniority's docstring for why).
        centroids: Precomputed category centroids (see
            core.classification.embeddings.build_centroids) — built
            once per batch, not once per title.
        qa_category_map: The classification-category -> qa_category
            mapping (see load_qa_category_map).
        adapters: Every available LLM adapter, keyed by provider — only
            touched if both the rules and embedding stages decline.
        embedding_base_url: Ollama server base URL.
        embedding_model: Ollama embedding model tag.
        http_client: The HTTP client for embedding calls.

    Returns:
        The resolved `Classification`.
    """
    seniority_band = derive_seniority_band(title)

    if title is None:
        # Nothing for the rules/embedding/LLM stages to work with — a
        # missing title resolves straight to "other" via the rules
        # stage rather than cascading all the way to an LLM call on a
        # placeholder string. (This diverges from the task-5 brief's
        # literal code, which fell through to classify_by_llm("Untitled
        # posting", ...) here: that contradicts this module's own unit
        # test — test_none_title_classifies_as_other_via_rules_without_
        # calling_llm asserts the LLM adapter is never called for a
        # `None` title — and would spend a real LLM call on every
        # titleless posting for no signal beyond a placeholder string.)
        return Classification(
            category="other",
            category_confidence=0.0,
            category_method="rules",
            qa_category=qa_category_map.get("other"),
            seniority_band=seniority_band,
        )

    category = classify_by_rules(title)
    if category is not None:
        confidence, method = 0.9, "rules"
    else:
        embedding_result = (
            classify_by_embedding(
                title,
                centroids,
                base_url=embedding_base_url,
                model=embedding_model,
                client=http_client,
            )
            if centroids
            else None
        )
        if embedding_result is not None:
            category, confidence = embedding_result
            method = "embedding"
        else:
            category, confidence = classify_by_llm(title, adapters=adapters)
            method = "llm"

    return Classification(
        category=category,
        category_confidence=confidence,
        category_method=method,
        qa_category=qa_category_map.get(category),
        seniority_band=seniority_band,
    )
