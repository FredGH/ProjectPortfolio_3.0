"""Tests for LLM adapters and call logging."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import httpx

from core.llm.adapters.anthropic import AnthropicAdapter
from core.llm.adapters.ollama import OllamaAdapter
from core.llm.call_log import log_llm_call
from core.llm.types import LLMResponse


class TestOllamaAdapter(unittest.TestCase):
    """Test Ollama adapter request/response parsing."""

    def test_complete_parses_ollama_response_shape(self) -> None:
        """Verify Ollama adapter parses response shape correctly."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/generate")
            return httpx.Response(
                200,
                json={
                    "response": "hello from ollama",
                    "prompt_eval_count": 12,
                    "eval_count": 7,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OllamaAdapter(base_url="http://ollama:11434", client=client)

        result = adapter.complete(model="llama3.1:8b", prompt="say hello")

        self.assertEqual(result.text, "hello from ollama")
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model, "llama3.1:8b")
        self.assertEqual(result.input_tokens, 12)
        self.assertEqual(result.output_tokens, 7)

    def test_complete_sends_temperature_and_seed_in_options(self) -> None:
        captured_json: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_json
            captured_json = json.loads(request.content)
            return httpx.Response(
                200, json={"response": "ok", "prompt_eval_count": 1, "eval_count": 1}
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OllamaAdapter(base_url="http://ollama:11434", client=client)

        adapter.complete(model="llama3.1:8b", prompt="hi", temperature=0.2, seed=42)

        self.assertEqual(captured_json["options"]["temperature"], 0.2)
        self.assertEqual(captured_json["options"]["seed"], 42)

    def test_complete_without_seed_omits_it_from_options(self) -> None:
        captured_json: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_json
            captured_json = json.loads(request.content)
            return httpx.Response(
                200, json={"response": "ok", "prompt_eval_count": 1, "eval_count": 1}
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OllamaAdapter(base_url="http://ollama:11434", client=client)

        adapter.complete(model="llama3.1:8b", prompt="hi")

        self.assertNotIn("seed", captured_json["options"])
        self.assertEqual(captured_json["options"]["temperature"], 0.0)


class TestAnthropicAdapter(unittest.TestCase):
    """Test Anthropic adapter request/response parsing."""

    def test_complete_parses_anthropic_response_shape(self) -> None:
        """Verify Anthropic adapter parses response shape correctly."""
        fake_message = mock.Mock()
        fake_message.content = [mock.Mock(text="hello from claude")]
        fake_message.usage = mock.Mock(input_tokens=20, output_tokens=9)

        fake_client = mock.Mock()
        fake_client.messages.create.return_value = fake_message

        adapter = AnthropicAdapter(api_key="test-key", client=fake_client)
        result = adapter.complete(model="claude-sonnet-5", prompt="say hello")

        self.assertEqual(result.text, "hello from claude")
        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(result.input_tokens, 20)
        self.assertEqual(result.output_tokens, 9)
        fake_client.messages.create.assert_called_once_with(
            model="claude-sonnet-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": "say hello"}],
            temperature=0.0,
        )

    def test_complete_sends_temperature_but_not_seed(self) -> None:
        fake_message = mock.Mock()
        fake_message.content = [mock.Mock(text="hello from claude")]
        fake_message.usage = mock.Mock(input_tokens=20, output_tokens=9)

        fake_client = mock.Mock()
        fake_client.messages.create.return_value = fake_message

        adapter = AnthropicAdapter(api_key="test-key", client=fake_client)
        adapter.complete(
            model="claude-sonnet-5", prompt="say hello", temperature=0.5, seed=42
        )

        fake_client.messages.create.assert_called_once_with(
            model="claude-sonnet-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": "say hello"}],
            temperature=0.5,
        )


class TestLogLlmCall(unittest.TestCase):
    """Test structured LLM call logging."""

    def test_returns_and_logs_structured_record(self) -> None:
        """Verify log_llm_call returns record and emits INFO log."""
        response = LLMResponse(
            text="hi",
            provider="ollama",
            model="llama3.1:8b",
            input_tokens=1,
            output_tokens=1,
        )
        with self.assertLogs("core.llm.call_log", level="INFO") as captured:
            record = log_llm_call(
                task="skill_extraction",
                response=response,
                prompt_version="local.v1",
            )

        self.assertEqual(record["task"], "skill_extraction")
        self.assertEqual(record["provider"], "ollama")
        self.assertEqual(record["prompt_version"], "local.v1")
        self.assertEqual(record["input_tokens"], 1)
        self.assertEqual(record["output_tokens"], 1)
        self.assertTrue(any("skill_extraction" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
