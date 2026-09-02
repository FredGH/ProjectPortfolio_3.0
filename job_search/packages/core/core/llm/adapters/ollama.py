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

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Run one completion call against Ollama.

        Args:
            model: The Ollama model tag, e.g. "llama3.1:8b".
            prompt: The prompt text.

        Returns:
            The normalised `LLMResponse`.
        """
        response = self.client.post(
            f"{self.base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        response.raise_for_status()
        payload = response.json()
        return LLMResponse(
            text=payload["response"],
            provider="ollama",
            model=model,
            input_tokens=payload.get("prompt_eval_count", 0),
            output_tokens=payload.get("eval_count", 0),
        )
