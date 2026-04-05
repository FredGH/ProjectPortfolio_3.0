import unittest
from unittest.mock import MagicMock, call, patch

from src.agents import MAX_CONTEXT_CHARS, reporting_analyst, researcher, scriptwriter

# ---------------------------------------------------------------------------
# Module-level token-consumption guard
# ---------------------------------------------------------------------------
# src/agents.py creates real anthropic.Anthropic() clients at import time.
# setUpModule() replaces every agent's _client with a MagicMock BEFORE any
# test runs.  This is the first line of defence: even if an individual test's
# patch.object accidentally fails, the real client is never reached and no
# tokens are consumed.  Each test class then replaces _client again with its
# own scoped mock in setUp(), and asserts the replacement is active before
# the test body runs.
# ---------------------------------------------------------------------------

def setUpModule():
    for agent in (researcher, reporting_analyst, scriptwriter):
        agent._client = MagicMock(name=f"module_guard_{agent.name}")

EXPECTED_OUTPUT = """\
## ⚠️ Challenges & Talent Gaps

-
The UK currently hosts approximately **17,000 professionals** with "Data Engineer" or "Senior Data Engineer" titles, representing a modest **2% year-over-year growth** — suggesting a potential talent shortage in a rapidly accelerating field.


-
The average tenure of a Data Engineer in the UK is just **1.8 years**, and one in three Data Engineers are actively open to exploring new career paths — highlighting significant retention challenges.


-
The UK Government's 2025 AI Labour Market Survey identified AI skills shortages and is using findings to support policy decisions to strengthen the UK's AI ecosystem under the **AI Opportunities Action Plan**.


---

> **Bottom Line:** The UK data job market in 2025 is characterised by rising salaries, strong AI integration, a shift toward senior and specialised roles, geographic expansion beyond London, and persistent talent shortages — making it one of the most competitive and opportunity-rich sectors in the country."""

INPUT_TOPIC = "Data job trends in the UK in 2025"


def _make_mock_response(text: str, input_tokens: int = 512, output_tokens: int = 256):
    """Build a minimal Anthropic Messages response mock."""
    block = MagicMock()
    block.text = text
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    response = MagicMock()
    response.content = [block]
    response.usage = usage
    return response


def _make_mock_logger():
    logger = MagicMock()
    logger.log = MagicMock()
    return logger


class TestResearcherAgent(unittest.TestCase):
    """Unit tests for the researcher agent.

    The Anthropic client on the module-level researcher instance is patched
    directly via patch.object so no real network calls are made.
    """

    def setUp(self):
        self.mock_client = MagicMock()
        self.patcher = patch.object(researcher, "_client", self.mock_client)
        self.patcher.start()
        self.assertIs(researcher._client, self.mock_client, "Real Anthropic client still active — mock did not apply")

    def tearDown(self):
        self.patcher.stop()

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_run_returns_expected_text(self):
        """researcher.run() returns the LLM text unchanged."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            EXPECTED_OUTPUT, input_tokens=512, output_tokens=256
        )

        text, _ = researcher.run(INPUT_TOPIC, _make_mock_logger())

        self.assertEqual(text, EXPECTED_OUTPUT)

    def test_run_returns_correct_usage(self):
        """researcher.run() returns input/output token counts from the response."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            EXPECTED_OUTPUT, input_tokens=512, output_tokens=256
        )

        _, usage = researcher.run(INPUT_TOPIC, _make_mock_logger())

        self.assertEqual(usage["input_tokens"], 512)
        self.assertEqual(usage["output_tokens"], 256)

    def test_run_sends_web_search_tool(self):
        """researcher.run() includes the web_search_20250305 tool in the API call."""
        self.mock_client.messages.create.return_value = _make_mock_response(EXPECTED_OUTPUT)

        researcher.run(INPUT_TOPIC, _make_mock_logger())

        call_kwargs = self.mock_client.messages.create.call_args.kwargs
        tool_types = [t.get("type") for t in call_kwargs.get("tools", [])]
        self.assertIn("web_search_20250305", tool_types)

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def test_run_truncates_long_prompt(self):
        """researcher.run() truncates prompts exceeding MAX_CONTEXT_CHARS."""
        self.mock_client.messages.create.return_value = _make_mock_response(EXPECTED_OUTPUT)
        oversized = "x" * (MAX_CONTEXT_CHARS + 500)
        mock_logger = _make_mock_logger()

        researcher.run(oversized, mock_logger)

        sent = self.mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertLessEqual(len(sent), MAX_CONTEXT_CHARS + 60)

        warning_calls = [c for c in mock_logger.log.call_args_list if c.args[1] == "warning"]
        self.assertTrue(warning_calls, "Expected a warning log for prompt truncation")

    def test_run_does_not_truncate_short_prompt(self):
        """researcher.run() leaves prompts under MAX_CONTEXT_CHARS untouched."""
        self.mock_client.messages.create.return_value = _make_mock_response(EXPECTED_OUTPUT)
        mock_logger = _make_mock_logger()

        researcher.run(INPUT_TOPIC, mock_logger)

        warning_calls = [c for c in mock_logger.log.call_args_list if c.args[1] == "warning"]
        self.assertFalse(warning_calls, "No warning expected for a short prompt")

    # ------------------------------------------------------------------
    # Retry / rate-limit handling
    # ------------------------------------------------------------------

    def test_run_retries_on_rate_limit_error(self):
        """researcher.run() retries once when a RateLimitError is raised."""
        import anthropic as anthropic_lib

        rate_limit_exc = anthropic_lib.RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
        self.mock_client.messages.create.side_effect = [
            rate_limit_exc,
            _make_mock_response(EXPECTED_OUTPUT),
        ]

        with patch("src.agents.time.sleep") as mock_sleep:
            text, _ = researcher.run(INPUT_TOPIC, _make_mock_logger())

        self.assertEqual(text, EXPECTED_OUTPUT)
        self.assertEqual(self.mock_client.messages.create.call_count, 2)
        mock_sleep.assert_called_once()

    def test_run_raises_after_all_retries_exhausted(self):
        """researcher.run() re-raises RateLimitError once all retries are spent."""
        import anthropic as anthropic_lib

        rate_limit_exc = anthropic_lib.RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
        # Always fail
        self.mock_client.messages.create.side_effect = rate_limit_exc

        with patch("src.agents.time.sleep"):
            with self.assertRaises(anthropic_lib.RateLimitError):
                researcher.run(INPUT_TOPIC, _make_mock_logger())


ANALYST_INPUT = "Data job trends in the UK in 2025"

ANALYST_EXPECTED_OUTPUT = """\
# UK Data Job Trends in 2025: A Comprehensive Market Analysis Report

---

**Prepared by:** Data Labour Market Reporting Division
**Reporting Period:** 2025 (with reference to Q1 2026 outlook)
**Classification:** Industry Intelligence Report
**Date of Publication:** 2025

---

## Table of Contents

1. Executive Summary
2. Market Overview
3. Geographic Distribution of Demand
4. Salary Landscape
5. The AI Influence on Data Roles
6. In-Demand Skills and Emerging Roles
7. Work Arrangements and Hiring Dynamics
8. Talent Gaps and Retention Challenges
9. Key Risks and Considerations
10. Strategic Recommendations
11. Conclusion

---

## 1. Executive Summary

The UK data job market in 2025 stands as one of the most dynamic, competitive, and opportunity-rich sectors in the country. Fuelled by accelerating AI adoption, record private sector investment, and the proliferation of data-driven decision-making across industries, demand for skilled data professionals continues to grow — even as the broader UK labour market shows signs of softening.

Mean salaries for data roles have reached **£50,412**, representing a **5.8% year-on-year increase**, outpacing the national average. The AI sector alone has seen revenues surge by **68% to £23.9 billion**, with employment rising **33% to 86,000 roles**. Against this backdrop, persistent talent shortages, high attrition rates, and a rapid evolution in required skill sets present significant challenges for employers, policymakers, and professionals alike.

This report synthesises the latest available data to provide a structured, evidence-based assessment of where the UK data job market stands in 2025, where it is heading, and what actions stakeholders should consider.

---

## 6. In-Demand Skills and Emerging Roles

### 6.1 The Shift from Research to Production

One of the most consequential structural changes in the UK data market in 2025 is the pivot from proof-of-concept AI development to production-ready deployment. Employers are no longer primarily seeking data scientists to build experimental models — they require **AI engineers** capable of deploying, scaling, and maintaining solutions in live environments.

This shift has given rise to a new generation of hybrid roles:

- **AI Product Engineers** — combining ML capability with product development skills
- **Machine Learning Infrastructure Specialists** — focusing on the reliability and scalability of ML systems
- **AI Translators** — bridging technical teams and business stakeholders to ensure AI solutions deliver measurable commercial value"""


class TestReportingAnalystAgent(unittest.TestCase):
    """Unit tests for the reporting_analyst agent.

    The Anthropic client on the module-level reporting_analyst instance is
    patched directly via patch.object — no real network calls are made.
    """

    def setUp(self):
        self.mock_client = MagicMock()
        self.patcher = patch.object(reporting_analyst, "_client", self.mock_client)
        self.patcher.start()
        self.assertIs(reporting_analyst._client, self.mock_client, "Real Anthropic client still active — mock did not apply")

    def tearDown(self):
        self.patcher.stop()

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_run_returns_expected_text(self):
        """reporting_analyst.run() returns the mocked report text unchanged."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            ANALYST_EXPECTED_OUTPUT, input_tokens=1024, output_tokens=512
        )

        text, _ = reporting_analyst.run(ANALYST_INPUT, _make_mock_logger())

        self.assertEqual(text, ANALYST_EXPECTED_OUTPUT)

    def test_run_returns_correct_usage(self):
        """reporting_analyst.run() returns input/output token counts from the response."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            ANALYST_EXPECTED_OUTPUT, input_tokens=1024, output_tokens=512
        )

        _, usage = reporting_analyst.run(ANALYST_INPUT, _make_mock_logger())

        self.assertEqual(usage["input_tokens"], 1024)
        self.assertEqual(usage["output_tokens"], 512)

    def test_run_does_not_use_web_search(self):
        """reporting_analyst.run() must NOT include any tools in the API call."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            ANALYST_EXPECTED_OUTPUT
        )

        reporting_analyst.run(ANALYST_INPUT, _make_mock_logger())

        call_kwargs = self.mock_client.messages.create.call_args.kwargs
        self.assertNotIn("tools", call_kwargs, "Reporting analyst should not use tools")

    def test_run_output_contains_executive_summary(self):
        """Report output contains the expected Executive Summary section."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            ANALYST_EXPECTED_OUTPUT, input_tokens=1024, output_tokens=512
        )

        text, _ = reporting_analyst.run(ANALYST_INPUT, _make_mock_logger())

        self.assertIn("Executive Summary", text)

    def test_run_output_contains_salary_data(self):
        """Report output references the £50,412 mean salary figure."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            ANALYST_EXPECTED_OUTPUT, input_tokens=1024, output_tokens=512
        )

        text, _ = reporting_analyst.run(ANALYST_INPUT, _make_mock_logger())

        self.assertIn("£50,412", text)

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def test_run_truncates_long_prompt(self):
        """reporting_analyst.run() truncates prompts exceeding MAX_CONTEXT_CHARS."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            ANALYST_EXPECTED_OUTPUT
        )
        oversized = "x" * (MAX_CONTEXT_CHARS + 500)
        mock_logger = _make_mock_logger()

        reporting_analyst.run(oversized, mock_logger)

        sent = self.mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertLessEqual(len(sent), MAX_CONTEXT_CHARS + 60)

        warning_calls = [c for c in mock_logger.log.call_args_list if c.args[1] == "warning"]
        self.assertTrue(warning_calls, "Expected a warning log for prompt truncation")

    def test_run_does_not_truncate_short_prompt(self):
        """reporting_analyst.run() leaves prompts under MAX_CONTEXT_CHARS untouched."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            ANALYST_EXPECTED_OUTPUT
        )
        mock_logger = _make_mock_logger()

        reporting_analyst.run(ANALYST_INPUT, mock_logger)

        warning_calls = [c for c in mock_logger.log.call_args_list if c.args[1] == "warning"]
        self.assertFalse(warning_calls, "No warning expected for a short prompt")

    # ------------------------------------------------------------------
    # Retry / rate-limit handling
    # ------------------------------------------------------------------

    def test_run_retries_on_rate_limit_error(self):
        """reporting_analyst.run() retries once when a RateLimitError is raised."""
        import anthropic as anthropic_lib

        rate_limit_exc = anthropic_lib.RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
        self.mock_client.messages.create.side_effect = [
            rate_limit_exc,
            _make_mock_response(ANALYST_EXPECTED_OUTPUT),
        ]

        with patch("src.agents.time.sleep") as mock_sleep:
            text, _ = reporting_analyst.run(ANALYST_INPUT, _make_mock_logger())

        self.assertEqual(text, ANALYST_EXPECTED_OUTPUT)
        self.assertEqual(self.mock_client.messages.create.call_count, 2)
        mock_sleep.assert_called_once()

    def test_run_raises_after_all_retries_exhausted(self):
        """reporting_analyst.run() re-raises RateLimitError once all retries are spent."""
        import anthropic as anthropic_lib

        rate_limit_exc = anthropic_lib.RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
        self.mock_client.messages.create.side_effect = rate_limit_exc

        with patch("src.agents.time.sleep"):
            with self.assertRaises(anthropic_lib.RateLimitError):
                reporting_analyst.run(ANALYST_INPUT, _make_mock_logger())


SCRIPTWRITER_INPUT = "Data job trends in the UK in 2025"

SCRIPTWRITER_EXPECTED_OUTPUT = """\
**Priya:** So today, we are diving deep into UK data job trends in 2025. We've got a big meaty report in front of us, and honestly? There is some wild stuff in here.

**Jamie:** Wild is an understatement. We're talking salaries, AI taking over, regional uprisings—

**Priya:** Regional uprisings. Jamie, it's a labour market report, not Game of Thrones.

**Jamie:** *(laughing)* Have you SEEN what's happening in the North East? I'm telling you, it's a whole thing. We'll get there.

**Priya:** Okay, okay. Let's start from the top. Big picture — how is the UK data job market doing in 2025?

**Jamie:** Short answer? Really well. Like, surprisingly well when you consider that the broader UK jobs market is actually a bit... rough.

**Priya:** Yeah, job postings across the whole economy are still sitting about 19% below pre-pandemic levels. Which is sobering.

**Jamie:** But then you look at data roles — data scientists, data engineers, ML engineers, AI specialists — and it's almost like they're in a completely different economy.

**Priya:** A protected little bubble.

**Jamie:** A beautiful, well-compensated bubble. Roles in data, AI, and cybersecurity are projected to grow 15 to 20% in 2025. While everyone else is out here refreshing their LinkedIn in a panic.

**Priya:** *(laughing)* Okay, so the data world is doing well. Let's talk money, because I know that's what half our listeners actually tuned in for.

**Jamie:** Right, so the mean salary for data roles right now is £50,412.

**Jamie:** Until next time, keep your pipelines clean and your models honest.

**Priya:** *(laughing)* Oh god. Goodbye everyone.

**[OUTRO MUSIC]**

---

*Data & Chill is an independent podcast. All salary figures and statistics referenced in this episode are drawn from the UK Data Job Trends Report 2025.*"""


class TestScriptwriterAgent(unittest.TestCase):
    """Unit tests for the scriptwriter agent.

    The Anthropic client on the module-level scriptwriter instance is
    patched directly via patch.object — no real network calls are made.
    """

    def setUp(self):
        self.mock_client = MagicMock()
        self.patcher = patch.object(scriptwriter, "_client", self.mock_client)
        self.patcher.start()
        self.assertIs(scriptwriter._client, self.mock_client, "Real Anthropic client still active — mock did not apply")

    def tearDown(self):
        self.patcher.stop()

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_run_returns_expected_text(self):
        """scriptwriter.run() returns the mocked script text unchanged."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            SCRIPTWRITER_EXPECTED_OUTPUT, input_tokens=2048, output_tokens=1024
        )

        text, _ = scriptwriter.run(SCRIPTWRITER_INPUT, _make_mock_logger())

        self.assertEqual(text, SCRIPTWRITER_EXPECTED_OUTPUT)

    def test_run_returns_correct_usage(self):
        """scriptwriter.run() returns input/output token counts from the response."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            SCRIPTWRITER_EXPECTED_OUTPUT, input_tokens=2048, output_tokens=1024
        )

        _, usage = scriptwriter.run(SCRIPTWRITER_INPUT, _make_mock_logger())

        self.assertEqual(usage["input_tokens"], 2048)
        self.assertEqual(usage["output_tokens"], 1024)

    def test_run_does_not_use_web_search(self):
        """scriptwriter.run() must NOT include any tools in the API call."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            SCRIPTWRITER_EXPECTED_OUTPUT
        )

        scriptwriter.run(SCRIPTWRITER_INPUT, _make_mock_logger())

        call_kwargs = self.mock_client.messages.create.call_args.kwargs
        self.assertNotIn("tools", call_kwargs, "Scriptwriter should not use tools")

    def test_run_output_contains_two_speakers(self):
        """Script output contains dialogue from both Priya and Jamie."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            SCRIPTWRITER_EXPECTED_OUTPUT, input_tokens=2048, output_tokens=1024
        )

        text, _ = scriptwriter.run(SCRIPTWRITER_INPUT, _make_mock_logger())

        self.assertIn("Priya", text)
        self.assertIn("Jamie", text)

    def test_run_output_contains_salary_reference(self):
        """Script output references the £50,412 mean salary figure from the report."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            SCRIPTWRITER_EXPECTED_OUTPUT, input_tokens=2048, output_tokens=1024
        )

        text, _ = scriptwriter.run(SCRIPTWRITER_INPUT, _make_mock_logger())

        self.assertIn("£50,412", text)

    def test_run_output_is_substantial(self):
        """Script is long enough to constitute a real podcast episode (> 1000 chars)."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            SCRIPTWRITER_EXPECTED_OUTPUT, input_tokens=2048, output_tokens=1024
        )

        text, _ = scriptwriter.run(SCRIPTWRITER_INPUT, _make_mock_logger())

        self.assertGreater(len(text), 1000)

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def test_run_truncates_long_prompt(self):
        """scriptwriter.run() truncates prompts exceeding MAX_CONTEXT_CHARS."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            SCRIPTWRITER_EXPECTED_OUTPUT
        )
        oversized = "x" * (MAX_CONTEXT_CHARS + 500)
        mock_logger = _make_mock_logger()

        scriptwriter.run(oversized, mock_logger)

        sent = self.mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertLessEqual(len(sent), MAX_CONTEXT_CHARS + 60)

        warning_calls = [c for c in mock_logger.log.call_args_list if c.args[1] == "warning"]
        self.assertTrue(warning_calls, "Expected a warning log for prompt truncation")

    def test_run_does_not_truncate_short_prompt(self):
        """scriptwriter.run() leaves prompts under MAX_CONTEXT_CHARS untouched."""
        self.mock_client.messages.create.return_value = _make_mock_response(
            SCRIPTWRITER_EXPECTED_OUTPUT
        )
        mock_logger = _make_mock_logger()

        scriptwriter.run(SCRIPTWRITER_INPUT, mock_logger)

        warning_calls = [c for c in mock_logger.log.call_args_list if c.args[1] == "warning"]
        self.assertFalse(warning_calls, "No warning expected for a short prompt")

    # ------------------------------------------------------------------
    # Retry / rate-limit handling
    # ------------------------------------------------------------------

    def test_run_retries_on_rate_limit_error(self):
        """scriptwriter.run() retries once when a RateLimitError is raised."""
        import anthropic as anthropic_lib

        rate_limit_exc = anthropic_lib.RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
        self.mock_client.messages.create.side_effect = [
            rate_limit_exc,
            _make_mock_response(SCRIPTWRITER_EXPECTED_OUTPUT),
        ]

        with patch("src.agents.time.sleep") as mock_sleep:
            text, _ = scriptwriter.run(SCRIPTWRITER_INPUT, _make_mock_logger())

        self.assertEqual(text, SCRIPTWRITER_EXPECTED_OUTPUT)
        self.assertEqual(self.mock_client.messages.create.call_count, 2)
        mock_sleep.assert_called_once()

    def test_run_raises_after_all_retries_exhausted(self):
        """scriptwriter.run() re-raises RateLimitError once all retries are spent."""
        import anthropic as anthropic_lib

        rate_limit_exc = anthropic_lib.RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
        self.mock_client.messages.create.side_effect = rate_limit_exc

        with patch("src.agents.time.sleep"):
            with self.assertRaises(anthropic_lib.RateLimitError):
                scriptwriter.run(SCRIPTWRITER_INPUT, _make_mock_logger())


if __name__ == "__main__":
    unittest.main()
