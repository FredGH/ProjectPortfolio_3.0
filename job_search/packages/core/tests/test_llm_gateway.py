from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.llm.gateway import complete
from core.llm.task_config import TaskConfigError
from core.llm.types import LLMResponse

_SAMPLE_YAML = """
tasks:
  skill_extraction:
    provider: fake
    model: fake-model-v1
    prompt_family: local
"""


class _FakeAdapter:
    """Fake adapter for testing gateway routing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, model: str, prompt: str) -> LLMResponse:
        """Record call and return fake response."""
        self.calls.append((model, prompt))
        return LLMResponse(
            text=f"echo: {prompt}",
            provider="fake",
            model=model,
            input_tokens=3,
            output_tokens=5,
        )


class TestGatewayComplete(unittest.TestCase):
    """Tests for the LLM gateway complete function."""

    def setUp(self) -> None:
        """Create temp config file and fake adapter."""
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        tmp.write(_SAMPLE_YAML)
        tmp.close()
        self.config_path = Path(tmp.name)
        self.fake_adapter = _FakeAdapter()

    def tearDown(self) -> None:
        """Clean up temp config file."""
        self.config_path.unlink(missing_ok=True)

    def test_routes_to_the_configured_adapter_and_returns_its_response(
        self,
    ) -> None:
        """Test complete routes to configured adapter and returns response."""
        result = complete(
            "skill_extraction",
            "extract skills from this JD",
            prompt_version="local.v1",
            adapters={"fake": self.fake_adapter},
            config_path=self.config_path,
        )

        self.assertEqual(result.text, "echo: extract skills from this JD")
        self.assertEqual(
            self.fake_adapter.calls,
            [("fake-model-v1", "extract skills from this JD")],
        )

    def test_raises_when_task_has_no_config_entry(self) -> None:
        """Test complete raises TaskConfigError for unknown task."""
        with self.assertRaises(TaskConfigError):
            complete(
                "unknown_task",
                "prompt",
                prompt_version="local.v1",
                adapters={"fake": self.fake_adapter},
                config_path=self.config_path,
            )

    def test_raises_when_configured_provider_has_no_adapter_supplied(
        self,
    ) -> None:
        """Test complete raises KeyError when adapter not supplied."""
        with self.assertRaises(KeyError):
            complete(
                "skill_extraction",
                "prompt",
                prompt_version="local.v1",
                adapters={},  # no "fake" adapter provided
                config_path=self.config_path,
            )


if __name__ == "__main__":
    unittest.main()
