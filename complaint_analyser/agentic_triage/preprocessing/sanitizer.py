from __future__ import annotations

import re

# Prompt-injection patterns stripped before any LLM call.
# Only sanitized_text (never raw_text) is passed to Ollama.
_PATTERNS = re.compile(
    r"ignore\s+(?:all\s+)?previous\s+instructions?"
    r"|system\s*:"
    r"|<\|im_start\|>"
    r"|<\|im_end\|>"
    r"|<\|system\|>"
    r"|#{3,}"
    r"|\[INST\]"
    r"|\[/INST\]"
    r"|<s>"
    r"|</s>",
    re.IGNORECASE,
)


def sanitize(text: str) -> str:
    cleaned = _PATTERNS.sub("", text).strip()
    return f"<complaint>{cleaned}</complaint>"
