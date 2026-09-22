"""Test doubles shared by the Step 14 tests."""

from __future__ import annotations

from core.llm.types import LLMResponse


class FakeAdapter:
    """An `LLMAdapter` that returns canned response texts.

    Attributes:
        calls: `(model, prompt)` for every `complete` call, in order.
        max_tokens_seen: The `max_tokens` of every call, in order.
    """

    def __init__(self, *responses: str, truncated: bool = False) -> None:
        """Initialise the fake.

        Args:
            *responses: Texts to return, one per call in order; the last
                one repeats once the list is exhausted.
            truncated: Whether every response is flagged as cut off by the
                output-token cap.
        """
        self._responses = list(responses)
        self._truncated = truncated
        self.calls: list[tuple[str, str]] = []
        self.max_tokens_seen: list[int | None] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Record the call and return the next canned response.

        Args:
            model: The model identifier.
            prompt: The prompt text.
            temperature: Unused.
            seed: Unused.
            max_tokens: Recorded in `max_tokens_seen`.

        Returns:
            The next canned `LLMResponse`.
        """
        self.calls.append((model, prompt))
        self.max_tokens_seen.append(max_tokens)
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return LLMResponse(
            text=self._responses[index],
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
            truncated=self._truncated,
        )
