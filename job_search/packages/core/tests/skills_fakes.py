"""Test doubles shared by the Step 14 tests."""

from __future__ import annotations

from core.llm.types import LLMResponse


class FakeAdapter:
    """An `LLMAdapter` that returns canned response texts.

    Attributes:
        calls: `(model, prompt)` for every `complete` call, in order.
        max_tokens_seen: The `max_tokens` of every call, in order.
    """

    def __init__(self, *responses: str, truncated: bool | list[bool] = False) -> None:
        """Initialise the fake.

        Args:
            *responses: Texts to return, one per call in order; the last
                one repeats once the list is exhausted.
            truncated: Whether a response is flagged as cut off by the
                output-token cap. A single bool applies to every call; a list
                applies per call in order (the last entry repeats once
                exhausted), so a test can express "the first call loops, the
                retry doesn't."
        """
        self._responses = list(responses)
        self._truncated = truncated if isinstance(truncated, list) else [truncated]
        self.calls: list[tuple[str, str]] = []
        self.max_tokens_seen: list[int | None] = []
        self.repeat_penalty_seen: list[float | None] = []
        self.repeat_last_n_seen: list[int | None] = []

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
        """Record the call and return the next canned response.

        Args:
            model: The model identifier.
            prompt: The prompt text.
            temperature: Unused.
            seed: Unused.
            max_tokens: Recorded in `max_tokens_seen`.
            repeat_penalty: Recorded in `repeat_penalty_seen`.
            repeat_last_n: Recorded in `repeat_last_n_seen`.

        Returns:
            The next canned `LLMResponse`.
        """
        self.calls.append((model, prompt))
        self.max_tokens_seen.append(max_tokens)
        self.repeat_penalty_seen.append(repeat_penalty)
        self.repeat_last_n_seen.append(repeat_last_n)
        call_index = len(self.calls) - 1
        response_index = min(call_index, len(self._responses) - 1)
        truncated_index = min(call_index, len(self._truncated) - 1)
        return LLMResponse(
            text=self._responses[response_index],
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
            truncated=self._truncated[truncated_index],
        )
