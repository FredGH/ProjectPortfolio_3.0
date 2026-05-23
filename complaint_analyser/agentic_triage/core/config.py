from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoringDimension:
    name: str
    description: str
    min_score: int = 0
    max_score: int = 5
    weight: float = 1.0
    high_score_examples: list[str] = field(default_factory=list)


@dataclass
class PriorityLevel:
    label: str
    min_composite: float
    description: str
    response_sla: str
    recommended_action: str


@dataclass
class CollectionConfig:
    name: str
    role: str
    top_k: int = 5
    filter_fields: list[str] = field(default_factory=list)
    search_mode: str = "hybrid"


@dataclass
class DomainConfig:
    domain_name: str
    input_field: str
    id_prefix: str
    scoring_dimensions: list[ScoringDimension]
    priority_levels: list[PriorityLevel]
    collections: list[CollectionConfig]
    confidence_threshold: float = 0.7
    max_reretrieval_loops: int = 2
    ner_labels: list[str] = field(default_factory=list)
    keyword_library_path: str | None = None
    system_prompt_template: str = ""
    use_hyde: bool = False
    multi_query_n: int = 0
