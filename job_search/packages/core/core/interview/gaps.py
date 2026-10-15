"""CV-gap scoring (PLAN.md Step 20): code only, no LLM.

A question's gap is how weakly the user's CV evidences the skills it tests:
the questions most likely to be fumbled surface first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.cv.schema import CVTruthBase

_ALIASES_PATH = Path(__file__).resolve().parents[4] / "config" / "skill_aliases.yml"
CURRENT_ROLE_EVIDENCE = 0.1
OLDER_ROLE_EVIDENCE = 0.2
LISTED_ONLY = 0.6
ABSENT = 1.0
NO_TAGS = 0.5


@dataclass(frozen=True)
class GapResult:
    """A question's gap score for one user.

    Attributes:
        score: 0 (strongly evidenced) to about 1.1 (absent and hard).
        missing_tags: Tags with no trace in the CV.
    """

    score: float
    missing_tags: list[str]


def load_alias_groups(path: Path | None = None) -> list[set[str]]:
    """Load groups of spellings that name one skill.

    Args:
        path: Override for tests; defaults to `config/skill_aliases.yml`.

    Returns:
        One set of lowercased spellings (label and aliases) per skill.
    """
    raw = yaml.safe_load((path or _ALIASES_PATH).read_text()) or {}
    groups = []
    for entry in raw.get("skills", []):
        spellings = {
            str(entry.get("label", "")).lower(),
            *map(str.lower, map(str, entry.get("aliases", []))),
        }
        groups.append({s for s in spellings if s})
    return groups


def _spellings(tag: str, groups: list[set[str]]) -> set[str]:
    """Expand a tag to every spelling of the same skill.

    Args:
        tag: A question tag.
        groups: Alias groups.

    Returns:
        The tag itself (lowercased) plus the rest of its alias group.
    """
    low = tag.strip().lower()
    for group in groups:
        if low in group:
            return set(group)
    return {low}


def _contains(text: str, spellings: set[str]) -> bool:
    """Say whether a text mentions any spelling as a whole word.

    Args:
        text: Text to search.
        spellings: Lowercased spellings.

    Returns:
        True when any spelling appears delimited by non-word characters.
    """
    low = text.lower()
    return any(
        re.search(rf"(?<![\w]){re.escape(s)}(?![\w])", low) for s in spellings if s
    )


def _tag_score(tag: str, truth_base: CVTruthBase, groups: list[set[str]]) -> float:
    """Score one tag against the CV.

    Args:
        tag: The tag.
        truth_base: The user's CV.
        groups: Alias groups.

    Returns:
        0.1 when a bullet of the current role evidences it, 0.2 for an older
        role, 0.6 when it is only a listed skill or tech, else 1.0.
    """
    spellings = _spellings(tag, groups)
    best = ABSENT
    for index, role in enumerate(truth_base.experience):
        if any(_contains(b.text, spellings) for b in role.bullets):
            best = min(
                best, CURRENT_ROLE_EVIDENCE if index == 0 else OLDER_ROLE_EVIDENCE
            )
        elif any(_contains(t, spellings) for t in role.tech):
            best = min(best, LISTED_ONLY)
    if any(_contains(s.name, spellings) for s in truth_base.skills):
        best = min(best, LISTED_ONLY)
    return best


def score_question(
    tags: list[str],
    difficulty: int,
    truth_base: CVTruthBase,
    groups: list[set[str]],
) -> GapResult:
    """Score one question for one user.

    Args:
        tags: The question's skill tags.
        difficulty: 1-5.
        truth_base: The user's CV.
        groups: Alias groups.

    Returns:
        The mean tag score scaled by difficulty (x 0.6 + 0.1 per level), and
        the tags absent from the CV. No tags scores neutral.
    """
    scores = [_tag_score(t, truth_base, groups) for t in tags]
    mean = sum(scores) / len(scores) if scores else NO_TAGS
    missing = [t for t, s in zip(tags, scores, strict=True) if s == ABSENT]
    return GapResult(mean * (0.6 + 0.1 * difficulty), missing)
