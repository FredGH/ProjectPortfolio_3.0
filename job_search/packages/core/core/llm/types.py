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
    """

    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMAdapter(Protocol):
    """The interface every provider adapter implements identically."""

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
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

        Returns:
            The normalised `LLMResponse`.
        """
        ...
