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
  job_categorisation:
    provider: anthropic
    model: claude-sonnet-5
    prompt_family: claude
    eval_metric: exact_match
    eval_regression_threshold: 0.05
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

    def test_resolves_eval_fields_when_present(self) -> None:
        """Test that eval_metric and eval_regression_threshold resolve.

        `job_categorisation` has both eval fields configured but no
        `local_*` fields, so those must default to `None`.
        """
        config = load_task_config("job_categorisation", config_path=self.config_path)
        self.assertEqual(config.eval_metric, "exact_match")
        self.assertEqual(config.eval_regression_threshold, 0.05)
        self.assertIsNone(config.local_provider)
        self.assertIsNone(config.local_model)
        self.assertIsNone(config.local_prompt_family)

    def test_eval_fields_default_to_none_when_absent(self) -> None:
        """Test that eval fields default to None when not in the YAML."""
        config = load_task_config("skill_extraction", config_path=self.config_path)
        self.assertIsNone(config.eval_metric)
        self.assertIsNone(config.eval_regression_threshold)


if __name__ == "__main__":
    unittest.main()
