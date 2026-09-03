from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.llm.task_config import TaskConfig, TaskConfigError, load_task_config

_SAMPLE_YAML = """
tasks:
  skill_extraction:
    provider: ollama
    model: llama3.1:8b
    prompt_family: local
  fabrication_critic:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
"""


class TestLoadTaskConfig(unittest.TestCase):
    """Test per-task LLM provider/model resolution."""

    def setUp(self) -> None:
        """Create a temporary YAML file for testing."""
        self._tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        self._tmp.write(_SAMPLE_YAML)
        self._tmp.close()
        self.config_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        """Clean up the temporary YAML file."""
        self.config_path.unlink(missing_ok=True)

    def test_resolves_a_known_task(self) -> None:
        """Test loading a known task configuration."""
        config = load_task_config("skill_extraction", config_path=self.config_path)
        self.assertEqual(
            config,
            TaskConfig(
                task="skill_extraction",
                provider="ollama",
                model="llama3.1:8b",
                prompt_family="local",
            ),
        )

    def test_resolves_a_second_known_task_on_a_different_provider(
        self,
    ) -> None:
        """Test loading a task with different provider."""
        config = load_task_config("fabrication_critic", config_path=self.config_path)
        self.assertEqual(config.provider, "anthropic")
        self.assertEqual(config.model, "claude-sonnet-5")

    def test_raises_on_unknown_task(self) -> None:
        """Test that TaskConfigError is raised for missing tasks."""
        with self.assertRaises(TaskConfigError):
            load_task_config("does_not_exist", config_path=self.config_path)


if __name__ == "__main__":
    unittest.main()
