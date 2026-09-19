"""Tolerant JSON parsing for LLM responses, shared by the extraction tasks.

Local models routinely don't return bare JSON despite being asked to
(observed from llama3.1:8b): a ```json fence, a fence preceded by prose,
or an object embedded in prose. Moved here from core.cv.extract so the CV
and JD-skill extractors share one implementation.
"""

from __future__ import annotations

import json
import re

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def parse_json_response(text: str) -> dict[str, object]:
    """Parse an LLM response into a JSON dict, tolerating common wrapping.

    Tries, in order: the text as-is; the first fenced code block; the
    substring from the first "{" to the last "}". Each candidate is a plain
    `json.loads` attempt — a candidate that parses but isn't the right shape
    still fails the caller's schema validation, so this never turns a
    malformed response into a false success.

    Args:
        text: The raw response text, already `.strip()`-ped.

    Returns:
        The parsed JSON value from the first candidate that parses.

    Raises:
        json.JSONDecodeError: If no candidate parses as JSON.
    """
    candidates = [text]
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        candidates.append(text[brace_start : brace_end + 1])

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    assert last_error is not None  # `candidates` always has >= 1 entry
    raise last_error
