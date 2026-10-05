"""Estimated API cost of an LLM call, from `config/llm_prices.yml`.

Prices are estimates; a model without a price has no estimate (`None`) —
a cost is never guessed.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_DEFAULT_PRICES_PATH = Path(__file__).resolve().parents[4] / "config" / "llm_prices.yml"


def estimate_cost_usd(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    config_path: Path | None = None,
) -> float | None:
    """Estimate what one call cost, in US dollars.

    Args:
        provider: `ollama` (local, free) or `anthropic`.
        model: The model identifier the call used.
        input_tokens: Prompt tokens.
        output_tokens: Completion tokens.
        config_path: Price-table override (tests). Defaults to
            `config/llm_prices.yml` at the repository root.

    Returns:
        `0.0` for a local (ollama) call; tokens x price per million for a
        priced model; `None` when the model has no price.
    """
    if provider == "ollama":
        return 0.0
    path = config_path or _DEFAULT_PRICES_PATH
    raw = yaml.safe_load(path.read_text()) or {}
    price = (raw.get("models") or {}).get(model)
    if not isinstance(price, dict):
        return None
    try:
        per_input = float(price["input"])
        per_output = float(price["output"])
    except (KeyError, TypeError, ValueError):
        return None
    return (input_tokens * per_input + output_tokens * per_output) / 1_000_000
