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
        """Verify Ollama adapter forwards temperature and seed as options."""
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
        """Verify Ollama adapter omits seed and defaults temperature to 0.0."""
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

    def _post_capturing(self, payload: dict, **complete_kwargs):
        """Run one complete() against a mock server; return (sent json, result)."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OllamaAdapter(base_url="http://ollama:11434", client=client)
        result = adapter.complete(model="llama3.1:8b", prompt="hi", **complete_kwargs)
        return captured, result

    def test_max_tokens_is_sent_as_num_predict(self) -> None:
        """A per-call output cap reaches Ollama as options.num_predict."""
        sent, _ = self._post_capturing({"response": "ok"}, max_tokens=2048)
        self.assertEqual(sent["options"]["num_predict"], 2048)

    def test_no_max_tokens_leaves_num_predict_unset(self) -> None:
        """Without a cap the request is unchanged (Ollama's own default applies)."""
        sent, _ = self._post_capturing({"response": "ok"})
        self.assertNotIn("num_predict", sent["options"])

    def test_a_reply_cut_off_by_the_cap_is_flagged_truncated(self) -> None:
        """Ollama's done_reason 'length' means the token cap stopped the reply."""
        _, result = self._post_capturing(
            {"response": '{"skills": [', "done_reason": "length"}, max_tokens=8
        )
        self.assertTrue(result.truncated)

    def test_a_reply_that_finished_is_not_truncated(self) -> None:
        """done_reason 'stop', or none at all, is a normal finish."""
        _, stopped = self._post_capturing({"response": "ok", "done_reason": "stop"})
        _, bare = self._post_capturing({"response": "ok"})
        self.assertFalse(stopped.truncated)
        self.assertFalse(bare.truncated)

    def test_repeat_penalty_and_repeat_last_n_are_sent_as_options(self) -> None:
        """A repetition penalty and its lookback window reach Ollama as-is."""
        sent, _ = self._post_capturing(
            {"response": "ok"}, repeat_penalty=1.15, repeat_last_n=256
        )
        self.assertEqual(sent["options"]["repeat_penalty"], 1.15)
        self.assertEqual(sent["options"]["repeat_last_n"], 256)

    def test_no_repeat_penalty_leaves_the_request_unchanged(self) -> None:
        """Without one, Ollama's own repeat_penalty/repeat_last_n defaults apply."""
        sent, _ = self._post_capturing({"response": "ok"})
        self.assertNotIn("repeat_penalty", sent["options"])
        self.assertNotIn("repeat_last_n", sent["options"])


class TestAnthropicAdapter(unittest.TestCase):
    """Test Anthropic adapter request/response parsing."""

    def _message(self, stop_reason: str = "end_turn") -> mock.Mock:
        message = mock.Mock()
        message.content = [mock.Mock(text="hi")]
        message.usage = mock.Mock(input_tokens=1, output_tokens=1)
        message.stop_reason = stop_reason
        return message

    def test_max_tokens_overrides_the_default_cap(self) -> None:
        """A per-call cap replaces the adapter's default max_tokens."""
        client = mock.Mock()
        client.messages.create.return_value = self._message()
        AnthropicAdapter(api_key="k", client=client).complete(
            model="claude-sonnet-5", prompt="hi", max_tokens=100
        )
        self.assertEqual(client.messages.create.call_args.kwargs["max_tokens"], 100)

    def test_a_reply_stopped_at_max_tokens_is_flagged_truncated(self) -> None:
        """Anthropic's stop_reason 'max_tokens' means the cap stopped the reply."""
        client = mock.Mock()
        client.messages.create.return_value = self._message("max_tokens")
        result = AnthropicAdapter(api_key="k", client=client).complete(
            model="claude-sonnet-5", prompt="hi", max_tokens=8
        )
        self.assertTrue(result.truncated)

    def test_a_reply_that_ended_normally_is_not_truncated(self) -> None:
        """stop_reason 'end_turn' is a normal finish."""
        client = mock.Mock()
        client.messages.create.return_value = self._message("end_turn")
        result = AnthropicAdapter(api_key="k", client=client).complete(
            model="claude-sonnet-5", prompt="hi"
        )
        self.assertFalse(result.truncated)

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
        """Verify Anthropic adapter forwards temperature but drops seed."""
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

    def test_repeat_penalty_and_repeat_last_n_are_accepted_and_ignored(self) -> None:
        """Anthropic has no such decoding knob; the call must not crash or leak them."""
        client = mock.Mock()
        client.messages.create.return_value = self._message()
        AnthropicAdapter(api_key="k", client=client).complete(
            model="claude-sonnet-5", prompt="hi", repeat_penalty=1.15, repeat_last_n=256
        )
        sent = client.messages.create.call_args.kwargs
        self.assertNotIn("repeat_penalty", sent)
        self.assertNotIn("repeat_last_n", sent)


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
