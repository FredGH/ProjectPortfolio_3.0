"""Anthropic adapter — target-provider completions via the Claude API."""

from __future__ import annotations

from typing import Any

from core.llm.types import LLMResponse

_MAX_TOKENS = 4096


class AnthropicAdapter:
    """Calls the Anthropic Messages API.

    Attributes:
        api_key: The Anthropic API key (used only when `client` isn't
            injected — tests always inject a fake client instead).
        client: Injected Anthropic SDK client (`anthropic.Anthropic`-shaped:
            exposes `.messages.create(...)`), so no test needs network
            access or a real API key.
    """

    def __init__(self, *, api_key: str | None, client: Any) -> None:
        """Initialise the adapter.

        Args:
            api_key: The Anthropic API key. May be `None` when `client` is
                already constructed (as in every unit test).
            client: The Anthropic SDK client to issue requests with.
        """
        self.api_key = api_key
        self.client = client

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
        """Run one completion call against Claude.

        Args:
            model: The Anthropic model identifier, e.g. "claude-sonnet-5".
            prompt: The prompt text.
            temperature: Ignored — verified live that the Anthropic Messages
                API now hard-rejects an explicit `temperature` for current
                models (400: "temperature is deprecated for this model", on
                claude-sonnet-5). Accepted (not rejected) so this adapter
                satisfies the same `LLMAdapter` Protocol as `OllamaAdapter`,
                which does support it.
            seed: Ignored — the Anthropic Messages API has no seed
                parameter, and Anthropic does not guarantee bit-for-bit
                reproducibility even at a fixed temperature. Accepted (not
                rejected) so this adapter satisfies the same `LLMAdapter`
                Protocol as `OllamaAdapter`, which does support it.
            max_tokens: Cap on the reply's length; defaults to this adapter's
                own limit. A reply stopped by it comes back `truncated`.
            repeat_penalty: Ignored — the Anthropic Messages API has no
                repetition-penalty parameter. Accepted so this adapter
                satisfies the same `LLMAdapter` Protocol as `OllamaAdapter`.
            repeat_last_n: Ignored, for the same reason as `repeat_penalty`.

        Returns:
            The normalised `LLMResponse`.
        """
        message = self.client.messages.create(
            model=model,
            max_tokens=max_tokens if max_tokens is not None else _MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(
            text=message.content[0].text,
            provider="anthropic",
            model=model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            truncated=message.stop_reason == "max_tokens",
        )
