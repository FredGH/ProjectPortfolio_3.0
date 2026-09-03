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

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Run one completion call against Claude.

        Args:
            model: The Anthropic model identifier, e.g. "claude-sonnet-5".
            prompt: The prompt text.

        Returns:
            The normalised `LLMResponse`.
        """
        message = self.client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return LLMResponse(
            text=message.content[0].text,
            provider="anthropic",
            model=model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )
