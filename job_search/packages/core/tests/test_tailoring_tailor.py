"""Unit tests for the Tailor: prompt rendering and the LLM call."""

from __future__ import annotations

import json
import tempfile
import unittest

from tests.tailoring_fixtures import (
    bullet_id,
    make_truth_base,
    write_pinned_task_config,
)

from core.llm.types import LLMResponse
from core.tailoring.schema import (
    JobContext,
    JobSkill,
    TailorBullet,
    TailorExperience,
    TailorOutput,
    TailorOutputError,
    TailorSummary,
)
from core.tailoring.tailor import (
    PROMPT_VERSION_NUMBER,
    TASK,
    merge_outputs,
    render_feedback,
    render_job_skills,
    render_truth_base,
    run_tailor,
)


class _ScriptedAdapter:
    """Returns canned replies in order and records every prompt."""

    def __init__(self, replies: list[str], *, truncated: bool = False) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.truncated = truncated

    def complete(self, *, model: str, prompt: str, **_: object) -> LLMResponse:
        self.prompts.append(prompt)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResponse(
            text=reply,
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
            truncated=self.truncated,
        )


def _job() -> JobContext:
    return JobContext(
        job_group_id="zzfixture-job",
        title_for_display="Lead Data Engineer",
        company="Gamma",
        description="Own the data platform.",
        skills=[
            JobSkill("s1", "dbt", "must_have"),
            JobSkill("s2", "Kubernetes", "nice_to_have"),
        ],
    )


class TestRenderers(unittest.TestCase):
    def setUp(self) -> None:
        self.truth_base = make_truth_base()

    def test_truth_base_rendering_numbers_roles_and_shows_bullet_ids(self) -> None:
        rendered = render_truth_base(self.truth_base)
        self.assertIn(
            "[0] Senior Data Engineer at Acme Bank (2019-01 – present)", rendered
        )
        self.assertIn("[1] Data Analyst at Beta Retail (2015-06 – 2018-12)", rendered)
        self.assertIn(
            f"({bullet_id(self.truth_base, 0, 0)}) Built dbt models for risk reporting",
            rendered,
        )
        self.assertIn("Skills: dbt, Airflow, SQL", rendered)

    def test_job_skills_rendering_includes_the_requirement_level(self) -> None:
        rendered = render_job_skills(_job().skills)
        self.assertIn("- dbt (must_have)", rendered)
        self.assertIn("- Kubernetes (nice_to_have)", rendered)

    def test_no_feedback_renders_empty_and_feedback_is_listed(self) -> None:
        self.assertEqual(render_feedback([]), "")
        rendered = render_feedback(["bullet e0b1 has no evidence", "surface SQL"])
        self.assertIn("Fix these problems from your previous attempt", rendered)
        self.assertIn("- bullet e0b1 has no evidence", rendered)
        self.assertIn("- surface SQL", rendered)


def _role(index: int, *texts: str) -> TailorExperience:
    return TailorExperience(
        truth_index=index, bullets=[TailorBullet(text=t) for t in texts]
    )


class TestMergeOutputs(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = TailorOutput(
            summary=TailorSummary(text="Old summary.", evidence_refs=["a"]),
            experience=[_role(0, "old 0"), _role(1, "old 1")],
            skills=["dbt", "SQL"],
        )

    def test_a_summary_only_patch_keeps_everything_else(self) -> None:
        patch = TailorOutput(summary=TailorSummary(text="New summary."))
        merged = merge_outputs(self.previous, patch)
        self.assertEqual(merged.summary.text, "New summary.")
        self.assertEqual(merged.experience, self.previous.experience)
        self.assertEqual(merged.skills, ["dbt", "SQL"])

    def test_a_role_patch_replaces_only_that_roles_whole_bullet_list(self) -> None:
        patch = TailorOutput(experience=[_role(1, "new 1a", "new 1b")])
        merged = merge_outputs(self.previous, patch)
        self.assertEqual(merged.summary, self.previous.summary)
        self.assertEqual(merged.experience[0], _role(0, "old 0"))
        self.assertEqual(merged.experience[1], _role(1, "new 1a", "new 1b"))
        self.assertEqual(len(merged.experience), 2)

    def test_a_skills_only_patch_replaces_the_skills(self) -> None:
        merged = merge_outputs(self.previous, TailorOutput(skills=["Airflow"]))
        self.assertEqual(merged.skills, ["Airflow"])
        self.assertEqual(merged.experience, self.previous.experience)

    def test_an_empty_patch_is_the_previous_output(self) -> None:
        self.assertEqual(merge_outputs(self.previous, TailorOutput()), self.previous)

    def test_a_role_the_previous_output_lacked_is_appended(self) -> None:
        previous = TailorOutput(experience=[_role(0, "old 0")])
        merged = merge_outputs(previous, TailorOutput(experience=[_role(1, "new")]))
        self.assertEqual([e.truth_index for e in merged.experience], [0, 1])

    def test_merging_does_not_mutate_its_inputs(self) -> None:
        before = self.previous.model_copy(deep=True)
        merge_outputs(self.previous, TailorOutput(experience=[_role(0, "x")]))
        self.assertEqual(self.previous, before)


class TestRunTailor(unittest.TestCase):
    def setUp(self) -> None:
        config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(config_dir.cleanup)
        self.config_path = write_pinned_task_config(config_dir.name)
        self.truth_base = make_truth_base()
        ref = bullet_id(self.truth_base, 0, 0)
        self.reply = json.dumps(
            {
                "summary": {"text": "Engineer.", "evidence_refs": [ref]},
                "experience": [
                    {
                        "truth_index": 0,
                        "bullets": [
                            {"text": "Built dbt models", "evidence_refs": [ref]}
                        ],
                    }
                ],
                "skills": ["dbt"],
            }
        )

    def test_returns_the_parsed_output_and_the_versions_used(self) -> None:
        adapter = _ScriptedAdapter([self.reply])
        result = run_tailor(
            self.truth_base,
            _job(),
            [],
            adapters={"ollama": adapter},
            config_path=self.config_path,
        )
        self.assertEqual(result.output.skills, ["dbt"])
        self.assertEqual(result.prompt_version, "local.v3")
        self.assertEqual(result.model, "llama3.1:8b")

    def test_a_backend_overrides_provider_model_and_prompt_family(self) -> None:
        from core.tailoring.backends import Backend

        # The pinned config says ollama/local; the backend says Claude.
        backend = Backend(
            "claude", "Claude", "anthropic", "claude-test", "claude", None
        )
        seen: list[str] = []

        class _Claude(_ScriptedAdapter):
            def complete(self, *, model: str, prompt: str, **kw: object):
                seen.append(model)
                return super().complete(model=model, prompt=prompt, **kw)

        result = run_tailor(
            self.truth_base,
            _job(),
            [],
            adapters={"anthropic": _Claude([self.reply])},
            config_path=self.config_path,
            backend=backend,
        )
        self.assertEqual(seen, ["claude-test"])
        self.assertEqual(result.prompt_version, "claude.v3")
        self.assertEqual(result.model, "claude-test")

    def test_the_prompt_carries_the_cv_the_job_and_the_feedback(self) -> None:
        adapter = _ScriptedAdapter([self.reply])
        run_tailor(
            self.truth_base,
            _job(),
            ["surface SQL"],
            adapters={"ollama": adapter},
            config_path=self.config_path,
        )
        prompt = adapter.prompts[0]
        self.assertIn("Target job title: Lead Data Engineer", prompt)
        self.assertIn("Own the data platform.", prompt)
        self.assertIn(bullet_id(self.truth_base, 0, 1), prompt)
        self.assertIn("- surface SQL", prompt)
        self.assertNotIn("{cv_text}", prompt)

    def test_a_retry_uses_the_patch_style_prompt_without_the_job_description(
        self,
    ) -> None:
        job = JobContext(
            job_group_id="zzfixture-job",
            title_for_display="Lead Data Engineer",
            company="Gamma",
            description="Own the data platform. " * 150,
            skills=_job().skills,
        )
        previous = TailorOutput(
            summary=TailorSummary(text="Prev summary.", evidence_refs=[]),
            experience=[_role(0, "prev bullet"), _role(1, "prev older")],
            skills=["dbt"],
        )
        first = _ScriptedAdapter([self.reply])
        run_tailor(
            self.truth_base,
            job,
            [],
            adapters={"ollama": first},
            config_path=self.config_path,
        )
        patch = json.dumps(
            {"experience": [{"truth_index": 1, "bullets": [{"text": "fixed"}]}]}
        )
        retry = _ScriptedAdapter([patch])
        result = run_tailor(
            self.truth_base,
            job,
            ["bullet e1b0 cites nothing"],
            adapters={"ollama": retry},
            config_path=self.config_path,
            previous=previous,
        )
        prompt = retry.prompts[0]
        self.assertIn("Prev summary.", prompt)
        self.assertIn("prev bullet", prompt)
        self.assertIn("- bullet e1b0 cites nothing", prompt)
        self.assertNotIn("Own the data platform", prompt)
        self.assertNotIn("null", prompt)
        self.assertLess(len(prompt), len(first.prompts[0]))
        self.assertEqual(result.prompt_version, "local.retry.v1")
        self.assertEqual(result.output.summary.text, "Prev summary.")
        self.assertEqual(result.output.experience[0], _role(0, "prev bullet"))
        self.assertEqual(result.output.experience[1], _role(1, "fixed"))
        self.assertEqual(result.output.skills, ["dbt"])

    def test_the_retry_prompts_format_with_their_keys(self) -> None:
        import re

        from core.llm.prompts import load_prompt

        for family in ("claude", "local"):
            with self.subTest(family=family):
                template = load_prompt(TASK, f"{family}.retry", 1)
                prompt = template.format(
                    cv_text="CV",
                    job_title="Lead Data Engineer",
                    job_skills="- SQL (must_have)",
                    previous_output="{}",
                    feedback="FB",
                )
                self.assertIsNone(re.search(r"\{[A-Za-z_]+\}", prompt))
                self.assertIn("removing the unsupported claim".lower(), prompt.lower())
                self.assertIn(
                    "do NOT write years of experience".lower(), prompt.lower()
                )
                self.assertIn("Cite the bullets it rests on", prompt)
                self.assertIn("keep the previous summary", prompt)
                self.assertIn("candidate's OWN summary text unchanged", prompt)
                self.assertIn('empty "evidence_refs" list', prompt)
        self.assertEqual(
            load_prompt(TASK, "claude.retry", 1), load_prompt(TASK, "local.retry", 1)
        )

    def test_a_truncated_reply_raises(self) -> None:
        adapter = _ScriptedAdapter([self.reply], truncated=True)
        with self.assertRaises(TailorOutputError):
            run_tailor(
                self.truth_base,
                _job(),
                [],
                adapters={"ollama": adapter},
                config_path=self.config_path,
            )

    def test_an_unusable_reply_raises(self) -> None:
        adapter = _ScriptedAdapter(["I cannot help with that."])
        with self.assertRaises(TailorOutputError):
            run_tailor(
                self.truth_base,
                _job(),
                [],
                adapters={"ollama": adapter},
                config_path=self.config_path,
            )

    def test_every_tailor_prompt_formats_with_the_tailor_keys(self) -> None:
        # The README's one-line switch to Claude must not break formatting.
        import re

        from core.llm.prompts import load_prompt

        for family in ("claude", "local"):
            for version in (1, 2, 3):
                with self.subTest(family=family, version=version):
                    template = load_prompt(TASK, family, version)
                    prompt = template.format(
                        cv_text="CV",
                        job_title="Lead Data Engineer",
                        job_description="Own it.",
                        job_skills="- SQL (must_have)",
                        feedback="",
                    )
                    self.assertIsNone(re.search(r"\{[A-Za-z_]+\}", prompt))
                    self.assertIn("Lead Data Engineer", prompt)

    def test_v2_prompts_carry_the_summary_rule_and_v1_does_not(self) -> None:
        from core.llm.prompts import load_prompt

        for family in ("claude", "local"):
            with self.subTest(family=family):
                v2 = load_prompt(TASK, family, 2)
                self.assertIn("do NOT write years of experience", v2)
                self.assertIn("removing the unsupported claim", v2)
                self.assertNotIn(
                    "do NOT write years of experience",
                    load_prompt(TASK, family, 1),
                )

    def test_the_live_config_records_claude_v3(self) -> None:
        adapter = _ScriptedAdapter([self.reply])
        result = run_tailor(
            self.truth_base,
            _job(),
            [],
            adapters={"anthropic": adapter},
        )
        self.assertEqual(PROMPT_VERSION_NUMBER, 3)
        self.assertEqual(result.prompt_version, "claude.v3")

    def test_the_task_is_registered_in_the_task_config(self) -> None:
        from core.llm.task_config import load_task_config

        self.assertEqual(load_task_config(TASK).task, "cv_tailoring")

    def test_the_live_config_routes_the_tailor_to_claude(self) -> None:
        from core.llm.prompts import load_prompt
        from core.llm.task_config import load_task_config

        config = load_task_config(TASK)
        self.assertEqual(config.provider, "anthropic")
        self.assertEqual(config.model, "claude-sonnet-5")
        # The configured prompt family's file exists and loads.
        self.assertTrue(load_prompt(TASK, config.prompt_family, PROMPT_VERSION_NUMBER))

    def test_the_critic_still_routes_to_anthropic(self) -> None:
        from core.llm.task_config import load_task_config

        self.assertEqual(load_task_config("fabrication_critic").provider, "anthropic")


if __name__ == "__main__":
    unittest.main()
