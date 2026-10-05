"""Unit tests for core.tailoring.backends (no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from core.tailoring.backends import (
    DOCKER_OLLAMA_URL,
    NATIVE_OLLAMA_URL,
    check_availability,
    default_backend_id,
    resolve_backends,
)

_CLAUDE_CFG = """tasks:
  cv_tailoring:
    provider: anthropic
    model: claude-x
    prompt_family: claude
    local_provider: ollama
    local_model: llama-l
    local_prompt_family: loc
"""
_OLLAMA_CFG = """tasks:
  cv_tailoring:
    provider: ollama
    model: llama-m
    prompt_family: local
"""


class TestBackends(unittest.TestCase):
    def _cfg(self, body: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "llm_tasks.yml"
        path.write_text(body)
        return path

    def _client(self, handler) -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_resolve_with_anthropic_config(self) -> None:
        b = resolve_backends(self._cfg(_CLAUDE_CFG))
        self.assertEqual(list(b), ["claude", "native", "docker"])
        self.assertEqual(
            (b["claude"].provider, b["claude"].model, b["claude"].prompt_family),
            ("anthropic", "claude-x", "claude"),
        )
        self.assertEqual(b["claude"].label, "Claude · claude-x")
        self.assertEqual(b["native"].base_url, NATIVE_OLLAMA_URL)
        self.assertEqual(b["docker"].base_url, DOCKER_OLLAMA_URL)
        self.assertEqual(
            (b["native"].model, b["native"].prompt_family), ("llama-l", "loc")
        )
        self.assertEqual(b["native"].label, "Ollama on this Mac · llama-l")
        self.assertEqual(b["docker"].label, "Docker Ollama (CPU only, slow) · llama-l")

    def test_resolve_with_ollama_config_and_no_local_fields(self) -> None:
        b = resolve_backends(self._cfg(_OLLAMA_CFG))
        self.assertEqual(b["claude"].model, "claude-sonnet-5")
        self.assertEqual(b["docker"].model, "llama-m")
        self.assertEqual(b["docker"].prompt_family, "local")

    def test_default_backend_id(self) -> None:
        self.assertEqual(default_backend_id(self._cfg(_CLAUDE_CFG)), "claude")
        self.assertEqual(default_backend_id(self._cfg(_OLLAMA_CFG)), "docker")

    def test_claude_availability_follows_the_key(self) -> None:
        claude = resolve_backends(self._cfg(_CLAUDE_CFG))["claude"]
        self.assertEqual(
            check_availability(claude, anthropic_available=True),
            (True, "API key configured"),
        )
        self.assertEqual(
            check_availability(claude, anthropic_available=False),
            (False, "ANTHROPIC_API_KEY is not set"),
        )

    def test_ollama_up_with_model(self) -> None:
        docker = resolve_backends(self._cfg(_CLAUDE_CFG))["docker"]
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"models": [{"name": "llama-l"}]})

        result = check_availability(
            docker, anthropic_available=False, http_client=self._client(handler)
        )
        self.assertEqual(result, (True, "reachable, model present"))
        self.assertEqual(seen, [f"{DOCKER_OLLAMA_URL}/api/tags"])

    def test_ollama_model_missing(self) -> None:
        native = resolve_backends(self._cfg(_CLAUDE_CFG))["native"]
        client = self._client(
            lambda r: httpx.Response(200, json={"models": [{"name": "other"}]})
        )
        self.assertEqual(
            check_availability(native, anthropic_available=False, http_client=client),
            (False, "model llama-l is not pulled"),
        )

    def test_ollama_down_and_timeout_never_raise(self) -> None:
        native = resolve_backends(self._cfg(_CLAUDE_CFG))["native"]

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        for handler in (refuse, slow):
            ok, detail = check_availability(
                native, anthropic_available=False, http_client=self._client(handler)
            )
            self.assertFalse(ok)
            self.assertEqual(detail, f"not reachable at {NATIVE_OLLAMA_URL}")

    def test_native_url_matches_the_api_constant(self) -> None:
        from app.dependencies import NATIVE_OLLAMA_BASE_URL

        self.assertEqual(NATIVE_OLLAMA_URL, NATIVE_OLLAMA_BASE_URL)


if __name__ == "__main__":
    unittest.main()
