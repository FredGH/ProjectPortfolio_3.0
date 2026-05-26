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
    escalate_if_any_dimension_exceeds: float | None = None


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

    @classmethod
    def from_dict(cls, data: dict) -> DomainConfig:
        return cls(
            domain_name=data["domain_name"],
            input_field=data["input_field"],
            id_prefix=data["id_prefix"],
            scoring_dimensions=[ScoringDimension(**d) for d in data["scoring_dimensions"]],
            priority_levels=[PriorityLevel(**d) for d in data["priority_levels"]],
            collections=[CollectionConfig(**d) for d in data["collections"]],
            confidence_threshold=data.get("confidence_threshold", 0.7),
            max_reretrieval_loops=data.get("max_reretrieval_loops", 2),
            ner_labels=data.get("ner_labels", []),
            keyword_library_path=data.get("keyword_library_path"),
            system_prompt_template=data.get("system_prompt_template", ""),
            use_hyde=data.get("use_hyde", False),
            multi_query_n=data.get("multi_query_n", 0),
        )
