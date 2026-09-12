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
        self.calls: list[tuple[str, str, float, int | None]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Record call and return fake response."""
        self.calls.append((model, prompt, temperature, seed))
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
            [("fake-model-v1", "extract skills from this JD", 0.0, None)],
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

    def test_defaults_to_zero_temperature_and_no_seed(self) -> None:
        """Test complete defaults to temperature 0.0 and no seed."""
        complete(
            "skill_extraction",
            "prompt",
            prompt_version="local.v1",
            adapters={"fake": self.fake_adapter},
            config_path=self.config_path,
        )
        self.assertEqual(
            self.fake_adapter.calls, [("fake-model-v1", "prompt", 0.0, None)]
        )

    def test_passes_through_a_given_temperature_and_seed(self) -> None:
        """Test complete passes a given temperature and seed to the adapter."""
        complete(
            "skill_extraction",
            "prompt",
            prompt_version="local.v1",
            adapters={"fake": self.fake_adapter},
            config_path=self.config_path,
            temperature=0.7,
            seed=42,
        )
        self.assertEqual(
            self.fake_adapter.calls, [("fake-model-v1", "prompt", 0.7, 42)]
        )

    def test_provider_and_model_override_bypass_task_config_resolution(
        self,
    ) -> None:
        """Test provider/model overrides bypass task config resolution.

        `skill_extraction`'s config_path entry routes to "fake" — override
        to a different adapter/model entirely, proving the override takes
        precedence over what the task's own config says.
        """
        other_adapter = _FakeAdapter()
        complete(
            "skill_extraction",
            "prompt",
            prompt_version="local.v1",
            adapters={"fake": self.fake_adapter, "other": other_adapter},
            config_path=self.config_path,
            provider="other",
            model="other-model-v9",
        )
        self.assertEqual(self.fake_adapter.calls, [])
        self.assertEqual(other_adapter.calls, [("other-model-v9", "prompt", 0.0, None)])


if __name__ == "__main__":
    unittest.main()
