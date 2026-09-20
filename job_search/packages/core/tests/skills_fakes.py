"""Test doubles shared by the Step 14 tests."""

from __future__ import annotations

from core.llm.types import LLMResponse


class FakeAdapter:
    """An `LLMAdapter` that returns canned response texts.

    Attributes:
        calls: `(model, prompt)` for every `complete` call, in order.
    """

    def __init__(self, *responses: str) -> None:
        """Initialise the fake.

        Args:
            *responses: Texts to return, one per call in order; the last
                one repeats once the list is exhausted.
        """
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Record the call and return the next canned response.

        Args:
            model: The model identifier.
            prompt: The prompt text.
            temperature: Unused.
            seed: Unused.

        Returns:
            The next canned `LLMResponse`.
        """
        self.calls.append((model, prompt))
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return LLMResponse(
            text=self._responses[index],
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )
