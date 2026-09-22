"""Shared types for the LLM gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    """The normalised shape every adapter returns, regardless of provider.

    Attributes:
        text: The completion text.
        provider: Which adapter produced this response ("ollama" or
            "anthropic").
        model: The provider-specific model identifier used.
        input_tokens: Prompt token count, as reported by the provider.
        output_tokens: Completion token count, as reported by the provider.
        truncated: True if the provider stopped the reply because it hit the
            output-token cap (`max_tokens`), so `text` is cut off — for a
            model stuck in a loop this is what ends the generation.
    """

    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    truncated: bool = False


class LLMAdapter(Protocol):
    """The interface every provider adapter implements identically."""

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
        repeat_penalty: float | None = None,
        repeat_last_n: int | None = None,
    ) -> LLMResponse:
        """Run one completion call.

        Args:
            model: The provider-specific model identifier.
            prompt: The prompt text.
            temperature: Sampling temperature. Defaults to 0.0 — the eval
                harness (PLAN.md Step 12a) depends on every call being as
                deterministic as the provider allows, so 0.0 is the
                default for every caller, not an eval-only opt-in.
            seed: A fixed seed, where the provider supports one. `None`
                means "no seed requested."
            max_tokens: Cap on the reply's length in tokens. `None` leaves
                the provider's own default. A reply stopped by the cap comes
                back with `LLMResponse.truncated` set.
            repeat_penalty: Penalty applied to tokens already seen within
                `repeat_last_n`, where the provider supports one. `None`
                leaves the provider's own default. At `temperature=0`
                (deterministic decoding), a repeating block of output longer
                than the lookback window can otherwise repeat forever — see
                `core.skills.jd_extract.REPEAT_PENALTY`.
            repeat_last_n: How many previous tokens `repeat_penalty` looks
                back over. `None` leaves the provider's own default.

        Returns:
            The normalised `LLMResponse`.
        """
        ...
