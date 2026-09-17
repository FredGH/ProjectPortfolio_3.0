"""Tests for core.cv.extract — the markdown->schema LLM step, plus a
Docling round-trip test that uses a synthetic HTML input (not a PDF) so
no fixture here is a CV-shaped document that could be confused with, or
need updating in lockstep with, real personal data.

Unit-level, fake adapter only — mirrors test_llm_classifier.py's own
`_FakeAdapter` pattern rather than mocking, since `core.llm.types.LLMAdapter`
is already a seam this codebase's tests implement directly.
"""

from __future__ import annotations

import json
import unittest

from core.cv.extract import docling_to_markdown, extract_truth_base
from core.llm.types import LLMResponse


class _FakeAdapter:
    """A fake `LLMAdapter` that returns a fixed response.

    Attributes:
        calls: `(model, prompt)` pairs passed to every `complete` call.
    """

    def __init__(self, response_text: str) -> None:
        """Initialise the fake adapter.

        Args:
            response_text: The text every `complete` call will return.
        """
        self._response_text = response_text
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Record the call and return the fixed fake response.

        Args:
            model: The provider-specific model identifier.
            prompt: The prompt text.
            temperature: Sampling temperature (unused by the fake).
            seed: A fixed seed (unused by the fake).

        Returns:
            The fixed `LLMResponse` configured at construction time.
        """
        self.calls.append((model, prompt))
        return LLMResponse(
            text=self._response_text,
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )


class TestDoclingToMarkdown(unittest.TestCase):
    def test_converts_html_to_markdown(self) -> None:
        html = b"<html><body><h1>Test Document</h1><p>Hello world.</p></body></html>"
        markdown = docling_to_markdown(html, "test.html")
        self.assertIn("Test Document", markdown)
        self.assertIn("Hello world.", markdown)


class TestExtractTruthBase(unittest.TestCase):
    def test_parses_llm_response_and_assigns_stable_bullet_ids(self) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "email": "jane.doe@example.com",
            "phone": "+44 7700 900000",
            "linkedin_url": "https://linkedin.com/in/janedoe",
            "nationality": "British",
            "locations": ["London, UK"],
            "work_auth": None,
            "skills": [{"name": "Python", "years": 5.0, "last_used": "2026-01"}],
            "experience": [
                {
                    "company": "Fixture Corp",
                    "title": "Test Engineer",
                    "start": "2020-01",
                    "end": None,
                    "bullets": ["Wrote extensive test fixtures."],
                    "tech": ["Python"],
                    "metrics": [],
                }
            ],
            "education": [],
            "qualifications": [],
            "publications": [],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.identity, "Jane Doe")
        self.assertEqual(result.email, "jane.doe@example.com")
        self.assertEqual(result.phone, "+44 7700 900000")
        self.assertEqual(result.linkedin_url, "https://linkedin.com/in/janedoe")
        self.assertEqual(result.nationality, "British")
        self.assertEqual(len(result.experience), 1)
        bullet = result.experience[0].bullets[0]
        self.assertEqual(bullet.text, "Wrote extensive test fixtures.")

        from core.cv.bullet_id import compute_bullet_id

        self.assertEqual(
            bullet.bullet_id,
            compute_bullet_id(0, "Wrote extensive test fixtures."),
        )

    def test_parses_summary_projects_qualifications_and_activities(
        self,
    ) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "summary": "Test engineer with 10 years of experience.",
            "qualifications": [{"name": "AI Engineering Track", "year": 2025}],
            "projects": [
                {
                    "name": "Test Fixture Generator",
                    "description": "Generates synthetic test fixtures.",
                    "tech": ["Python"],
                    "url": "https://example.com/fixtures",
                }
            ],
            "activities_interests": ["Mentor at MyJobGlasses"],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.summary, "Test engineer with 10 years of experience.")
        self.assertEqual(len(result.qualifications), 1)
        self.assertEqual(result.qualifications[0].name, "AI Engineering Track")
        self.assertEqual(len(result.projects), 1)
        self.assertEqual(result.projects[0].name, "Test Fixture Generator")
        self.assertEqual(result.projects[0].tech, ["Python"])
        self.assertEqual(result.activities_interests, ["Mentor at MyJobGlasses"])

    def test_raises_value_error_on_unparseable_response(self) -> None:
        fake_adapter = _FakeAdapter("not json")
        with self.assertRaises(ValueError):
            extract_truth_base(
                "# Jane Doe CV",
                adapters={"ollama": fake_adapter},
                provider="ollama",
                model="llama3.1:8b",
                prompt_family="local",
            )

    def test_strips_markdown_code_fence_before_parsing(self) -> None:
        payload = {"identity": "Jane Doe", "headline": "Senior Test Engineer"}
        fenced_response = f"```json\n{json.dumps(payload)}\n```"
        fake_adapter = _FakeAdapter(fenced_response)

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.identity, "Jane Doe")
        self.assertEqual(result.headline, "Senior Test Engineer")

    def test_extracts_json_from_a_fenced_block_preceded_by_prose(self) -> None:
        payload = {"identity": "Jane Doe", "headline": "Senior Test Engineer"}
        response_text = (
            f"Here is the extracted JSON:\n```json\n{json.dumps(payload)}\n```\n"
            "Let me know if you need anything else!"
        )
        fake_adapter = _FakeAdapter(response_text)

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.identity, "Jane Doe")

    def test_extracts_json_surrounded_by_prose_with_no_fence(self) -> None:
        payload = {"identity": "Jane Doe", "headline": "Senior Test Engineer"}
        response_text = f"Sure, here you go: {json.dumps(payload)} Hope that helps!"
        fake_adapter = _FakeAdapter(response_text)

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.identity, "Jane Doe")

    def test_tolerates_null_start_on_an_old_experience_entry(self) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "experience": [
                {
                    "company": "Old Corp",
                    "title": "Junior Engineer",
                    "start": None,
                    "end": "1998-06",
                    "bullets": [],
                    "tech": [],
                    "metrics": [],
                }
            ],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertIsNone(result.experience[0].start)
        self.assertEqual(result.experience[0].end, "1998-06")

    def test_tolerates_education_entry_missing_qualification(self) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "education": [
                {"institution": "The Open University", "start": "2002-08", "end": None}
            ],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.education[0].institution, "The Open University")
        self.assertIsNone(result.education[0].qualification)

    def test_recovers_publication_citation_keyed_as_title(self) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "publications": [
                {"title": "On XLE Index Construction, Springer"},
            ],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(
            result.publications[0].citation, "On XLE Index Construction, Springer"
        )

    def test_parses_publication_authors_and_year(self) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "publications": [
                {
                    "citation": "On XLE Index Construction, Springer",
                    "authors": ["Smith J.", "Doe A."],
                    "year": 2019,
                },
            ],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.publications[0].authors, ["Smith J.", "Doe A."])
        self.assertEqual(result.publications[0].year, 2019)

    def test_splits_a_combined_education_date_range(self) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "education": [
                {
                    "institution": "University of London",
                    "qualification": "MSc in Data Science",
                    "start": "2016-2017",
                    "end": None,
                }
            ],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.education[0].start, "2016")
        self.assertEqual(result.education[0].end, "2017")

    def test_does_not_split_a_start_date_that_is_not_a_combined_range(self) -> None:
        payload = {
            "identity": "Jane Doe",
            "headline": "Senior Test Engineer",
            "education": [
                {
                    "institution": "University of London",
                    "qualification": "MSc in Data Science",
                    "start": "2016",
                    "end": None,
                }
            ],
        }
        fake_adapter = _FakeAdapter(json.dumps(payload))

        result = extract_truth_base(
            "# Jane Doe CV",
            adapters={"ollama": fake_adapter},
            provider="ollama",
            model="llama3.1:8b",
            prompt_family="local",
        )

        self.assertEqual(result.education[0].start, "2016")
        self.assertIsNone(result.education[0].end)

    def test_error_includes_a_snippet_of_the_unparseable_response(self) -> None:
        fake_adapter = _FakeAdapter("Sorry, I can't help with that request.")
        with self.assertRaises(ValueError) as ctx:
            extract_truth_base(
                "# Jane Doe CV",
                adapters={"ollama": fake_adapter},
                provider="ollama",
                model="llama3.1:8b",
                prompt_family="local",
            )
        self.assertIn("Sorry, I can't help", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
