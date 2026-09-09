"""Rules-based title classification (PLAN.md Step 11a, stage 1) —
catches the ~70% of titles a keyword match resolves deterministically
and for free, before the more expensive embedding/LLM stages run.

Rule order matters: more specific categories (ai_ml_engineer,
data_scientist, analytics_engineer, platform_devops) are checked
before the broader software_engineer/data_engineer patterns, so e.g.
"Data Platform Engineer" matches data_engineer's "data platform"
phrase rather than falling through to a generic "engineer" match.
"""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"machine learning|\bml\b|\bai\b|deep learning|\bnlp\b|computer vision",
            re.IGNORECASE,
        ),
        "ai_ml_engineer",
    ),
    (
        re.compile(
            r"data scientist|applied scientist|quantitative analyst|"
            r"statistical analyst",
            re.IGNORECASE,
        ),
        "data_scientist",
    ),
    (
        re.compile(
            r"analytics engineer|business intelligence|\bbi engineer\b|"
            r"reporting analyst|analytics developer",
            re.IGNORECASE,
        ),
        "analytics_engineer",
    ),
    (
        re.compile(
            r"data engineer|\betl\b|data platform|data infrastructure|" r"big data",
            re.IGNORECASE,
        ),
        "data_engineer",
    ),
    (
        re.compile(
            r"devops|site reliability|\bsre\b|platform engineer|"
            r"infrastructure engineer|cloud engineer",
            re.IGNORECASE,
        ),
        "platform_devops",
    ),
    (
        re.compile(
            r"software engineer|software developer|backend|front[\s-]?end|"
            r"full[\s-]?stack|ios engineer|android engineer",
            re.IGNORECASE,
        ),
        "software_engineer",
    ),
]


def classify_by_rules(title: str | None) -> str | None:
    """Classify a title by keyword match, if one of the rules fires.

    Args:
        title: The job title to classify (title_for_display or
            title_raw — either works, since these rules match
            substrings and don't depend on decoration being stripped).

    Returns:
        The matched category, or `None` if no rule fires — the caller
        must cascade to the embedding stage next, never guess.
    """
    if title is None:
        return None
    for pattern, category in _RULES:
        if pattern.search(title):
            return category
    return None
