"""Ollama adapter — local completions via Ollama's HTTP API."""

from __future__ import annotations

import httpx

from core.llm.types import LLMResponse


class OllamaAdapter:
    """Calls a local Ollama server's `/api/generate` endpoint.

    Attributes:
        base_url: Base URL of the Ollama server.
        client: Injected HTTP client — a real `httpx.Client` at runtime, a
            `httpx.MockTransport`-backed one in tests, so no test needs a
            live Ollama server.
    """

    def __init__(self, *, base_url: str, client: httpx.Client) -> None:
        """Initialise the adapter.

        Args:
            base_url: Base URL of the Ollama server, e.g.
                "http://ollama:11434".
            client: The HTTP client to issue requests with.
        """
        self.base_url = base_url
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
        """Run one completion call against Ollama.

        Args:
            model: The Ollama model tag, e.g. "llama3.1:8b".
            prompt: The prompt text.
            temperature: Sampling temperature, sent as `options.temperature`.
            seed: A fixed seed, sent as `options.seed` when given — Ollama
                supports true seeded determinism, unlike Anthropic.
            max_tokens: Cap on the reply's length, sent as
                `options.num_predict` when given. Without it Ollama generates
                until the model stops, so a model stuck in a loop can run for
                minutes; a reply stopped by the cap comes back `truncated`.
            repeat_penalty: Sent as `options.repeat_penalty` when given.
                Ollama's own default lookback (`repeat_last_n`, 64 tokens) is
                too short to catch a longer repeating block, so without this
                the model can loop past the token cap instead of stopping.
            repeat_last_n: Sent as `options.repeat_last_n` when given — how
                many previous tokens `repeat_penalty` looks back over.

        Returns:
            The normalised `LLMResponse`.
        """
        options: dict[str, object] = {"temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if repeat_penalty is not None:
            options["repeat_penalty"] = repeat_penalty
        if repeat_last_n is not None:
            options["repeat_last_n"] = repeat_last_n
        response = self.client.post(
            f"{self.base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": options,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return LLMResponse(
            text=payload["response"],
            provider="ollama",
            model=model,
            input_tokens=payload.get("prompt_eval_count", 0),
            output_tokens=payload.get("eval_count", 0),
            truncated=payload.get("done_reason") == "length",
        )
