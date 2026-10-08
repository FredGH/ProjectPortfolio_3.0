# Step 18a — ATS .docx Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render an approved `TailoredDocument` to an ATS-safe `.docx` plus a `.txt` twin, verified against each other, via a new `render-cv` CLI command.

**Architecture:** A new `core/render/` package. `model.py` turns a `TailoredDocument` into a flat, ordered list of typed `Block`s (the single place that fixes section order and heading names; 18b will reuse it). `docx_ats.py` writes those blocks with python-docx using only plain paragraphs, so tables, text boxes, headers/footers and images have no code path. `service.py` writes both files, re-extracts the `.docx` text in XML order and fails if it differs from the `.txt` twin.

**Tech Stack:** Python 3.11, python-docx 1.2.0 (pulls in lxml), pydantic models from `core.tailoring.schema`, `unittest`.

**Spec:** [docs/superpowers/specs/2026-10-08-step18-cv-rendering-design.md](../specs/2026-10-08-step18-cv-rendering-design.md)

## Global Constraints

- The repo is **public**: tests use invented fixtures only (`Zz Fixture`); never commit a CV, template or output. `private/` and `output/` are git-ignored.
- Section headings are exactly: `Summary`, `Skills`, `Experience`, `Education`, `Projects`, `Publications`, `Certifications`, `Activities`, in that order, each only when non-empty.
- Dates render as `MM/YYYY – MM/YYYY` (en dash); open end is `Present`.
- Acronym plus expansion on first use only, from a curated dictionary; never applied to the name, headline (target title), contact line, role titles or role meta lines.
- Filename: `<surname>_<title_for_display>_<company>.docx`; company comes from `core.tailoring.context.load_job_context(...).company`.
- The renderer never edits wording, reads the database (the CLI does), or calls an LLM.
- Code rules (`.claude/rules/python-style.md`): Python 3.11, black/isort/ruff (88 cols), `from __future__ import annotations`, type hints, Google-style docstrings with `Args`/`Returns`/`Raises` on every function and class (private included).
- Tests (`.claude/rules/python-testing.md`): `unittest`, one file per module under `packages/core/tests/`, behaviour-named methods.
- Run tests from `packages/core` with the arm64 interpreter (this Mac's shell is x86_64 and the venv is arm64): `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.<module> -v`.
- Format only the files you touched: `arch -arm64 ../../venv/bin/ruff check <files>`, `black <files>`, `isort <files>` (never `isort .`).

## Review Focus

1. A role with `start`/`end` missing or in an unexpected shape (`Spring 2020`, `2019-13`): must not crash; shown verbatim (Task 1).
2. An acronym the author already expanded (`GCP (Google Cloud Platform)`), or one inside a longer token (`dbt/ELT`, `APIs`): no double expansion, no mangling (Task 1).
3. A company that is `None`, or a title/company with `&`, `/`, accents or path characters: a safe filename, still a valid `.docx` (Tasks 1, 3, 4).
4. Bullet text with newlines, tabs or double spaces: collapsed so the `.docx` text equals the `.txt` twin (Task 2).
5. Empty optional sections (no summary, education, projects): no empty headings (Task 2).

---

### Task 1: Text rules (dates, acronyms, filename)

**Files:**
- Create: `packages/core/core/render/__init__.py`
- Create: `packages/core/core/render/format.py`
- Test: `packages/core/tests/test_render_format.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `format_date_range(start: str | None, end: str | None) -> str`
  - `class AcronymExpander` with `__init__(self, acronyms: dict[str, str] | None = None) -> None` and `expand(self, text: str) -> str` (stateful: first use per instance)
  - `build_filename(identity: str, title: str, company: str | None, extension: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_render_format.py`:

```python
"""Unit tests for the CV rendering text rules."""

from __future__ import annotations

import unittest

from core.render.format import AcronymExpander, build_filename, format_date_range


class TestFormatDateRange(unittest.TestCase):
    def test_year_month_range(self) -> None:
        self.assertEqual(
            format_date_range("2019-01", "2022-12"), "01/2019 – 12/2022"
        )

    def test_missing_end_is_present(self) -> None:
        self.assertEqual(format_date_range("2019-01", None), "01/2019 – Present")

    def test_month_name_and_present_word_are_normalised(self) -> None:
        self.assertEqual(
            format_date_range("March 2024", "Present"), "03/2024 – Present"
        )

    def test_year_only_is_kept(self) -> None:
        self.assertEqual(format_date_range("2015", "2018"), "2015 – 2018")

    def test_unrecognised_text_is_kept_verbatim_and_logged(self) -> None:
        with self.assertLogs("core.render.format", level="WARNING"):
            result = format_date_range("Spring 2020", None)
        self.assertEqual(result, "Spring 2020 – Present")

    def test_invalid_month_is_kept_verbatim(self) -> None:
        with self.assertLogs("core.render.format", level="WARNING"):
            result = format_date_range("2019-13", "2020-01")
        self.assertEqual(result, "2019-13 – 01/2020")

    def test_no_dates_is_empty(self) -> None:
        self.assertEqual(format_date_range(None, None), "")

    def test_missing_start_shows_only_the_end(self) -> None:
        self.assertEqual(format_date_range(None, "2018-12"), "12/2018")


class TestAcronymExpander(unittest.TestCase):
    def test_expands_the_first_use_only(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("Built ELT pipelines and more ELT"),
            "Built ELT (Extract, Load, Transform) pipelines and more ELT",
        )

    def test_later_texts_are_left_alone(self) -> None:
        expander = AcronymExpander()
        expander.expand("ELT one")
        self.assertEqual(expander.expand("ELT two"), "ELT two")

    def test_an_author_given_expansion_is_kept_and_counts_as_first_use(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("GCP (Google Cloud Platform) migration"),
            "GCP (Google Cloud Platform) migration",
        )
        self.assertEqual(expander.expand("More GCP"), "More GCP")

    def test_an_unrelated_bracket_does_not_count_as_an_expansion(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("ELT (batch) jobs"),
            "ELT (Extract, Load, Transform) (batch) jobs",
        )

    def test_acronyms_inside_longer_tokens_are_not_touched(self) -> None:
        expander = AcronymExpander()
        for text in ("dbt/ELT models", "ELTA", "REST APIs"):
            self.assertEqual(expander.expand(text), text)

    def test_slash_acronym(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("Set up CI/CD for dbt"),
            "Set up CI/CD (Continuous Integration/Continuous Delivery) for dbt",
        )

    def test_two_acronyms_in_one_text(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("GCP and AWS"),
            "GCP (Google Cloud Platform) and AWS (Amazon Web Services)",
        )


class TestBuildFilename(unittest.TestCase):
    def test_surname_title_company(self) -> None:
        self.assertEqual(
            build_filename("Zz Fixture", "Lead Data Engineer", "Acme Bank", "docx"),
            "Fixture_Lead_Data_Engineer_Acme_Bank.docx",
        )

    def test_path_characters_and_symbols_are_removed(self) -> None:
        self.assertEqual(
            build_filename(
                "Zz Fixture", "Lead Data Engineer / Platform", "Acme & Sons Ltd.", "txt"
            ),
            "Fixture_Lead_Data_Engineer_Platform_Acme_Sons_Ltd.txt",
        )

    def test_accents_are_folded(self) -> None:
        self.assertEqual(
            build_filename("Zoë Müller", "Ingénieur Données", "Société", "docx"),
            "Muller_Ingenieur_Donnees_Societe.docx",
        )

    def test_missing_company_is_omitted(self) -> None:
        self.assertEqual(
            build_filename("Zz Fixture", "Lead Data Engineer", None, "docx"),
            "Fixture_Lead_Data_Engineer.docx",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_format -v`
Expected: ERROR `ModuleNotFoundError: No module named 'core.render'`

- [ ] **Step 3: Implement**

Create `packages/core/core/render/__init__.py` (empty file).

Create `packages/core/core/render/format.py`:

```python
"""Pure text rules for rendering a CV (Step 18): dates, acronym expansion on
first use, and the output filename."""

from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$")
_OPEN_END = frozenset({"present", "current", "now", "ongoing", "to date"})
_MONTHS = {
    name: number
    for number, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}

ACRONYMS = {
    "ELT": "Extract, Load, Transform",
    "ETL": "Extract, Transform, Load",
    "GCP": "Google Cloud Platform",
    "AWS": "Amazon Web Services",
    "CI/CD": "Continuous Integration/Continuous Delivery",
    "API": "Application Programming Interface",
    "NLP": "Natural Language Processing",
    "ML": "Machine Learning",
}
"""The only acronyms ever expanded. Deliberately small and curated."""


def _format_date(value: str) -> str:
    """Render one date as `MM/YYYY`, `YYYY` or `Present`.

    Args:
        value: A raw truth-base date such as `2019-01`, `2019`, `March 2024`
            or `Present`.

    Returns:
        The normalised date; an unrecognised value is returned unchanged
        (and logged) rather than guessed at.
    """
    text = value.strip()
    if text.lower() in _OPEN_END:
        return "Present"
    match = _YEAR_MONTH_RE.match(text)
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(2)}/{match.group(1)}"
    if _YEAR_RE.match(text):
        return text
    match = _MONTH_YEAR_RE.match(text)
    if match:
        month = _MONTHS.get(match.group(1)[:3].lower())
        if month:
            return f"{month:02d}/{match.group(2)}"
    logger.warning("date %r is not in a known format; kept verbatim", value)
    return text


def format_date_range(start: str | None, end: str | None) -> str:
    """Render a role's dates as `MM/YYYY – MM/YYYY`.

    Args:
        start: The raw start date, if any.
        end: The raw end date; None means the role is current.

    Returns:
        The range with an en dash, `Present` for an open end, only the end
        when there is no start, and an empty string when there are no dates.
    """
    if not start and not end:
        return ""
    right = _format_date(end) if end else "Present"
    if not start:
        return right
    return f"{_format_date(start)} – {right}"


class AcronymExpander:
    """Expand each known acronym once, on its first use in the document.

    Attributes:
        _acronyms: Acronym to expansion.
        _seen: Acronyms already met, expanded or not.
        _patterns: One whole-token regex per acronym.
    """

    def __init__(self, acronyms: dict[str, str] | None = None) -> None:
        """Create an expander with an empty first-use memory.

        Args:
            acronyms: Acronym to expansion; defaults to `ACRONYMS`.
        """
        self._acronyms = dict(ACRONYMS if acronyms is None else acronyms)
        self._seen: set[str] = set()
        self._patterns = {
            acronym: re.compile(rf"(?<![\w/]){re.escape(acronym)}(?![\w/])")
            for acronym in self._acronyms
        }

    def expand(self, text: str) -> str:
        """Expand acronyms in `text` that have not been seen before.

        An acronym already followed by its own expansion is left as written
        and counts as seen. An acronym inside a longer token (`dbt/ELT`,
        `APIs`) is not an acronym use and is ignored.

        Args:
            text: One block of document text, in document order.

        Returns:
            The text with first-use acronyms expanded.
        """
        for acronym, expansion in self._acronyms.items():
            if acronym in self._seen:
                continue
            match = self._patterns[acronym].search(text)
            if match is None:
                continue
            self._seen.add(acronym)
            after = text[match.end() :]
            if re.match(rf"\s*\(\s*{re.escape(expansion)}", after, re.IGNORECASE):
                continue
            text = f"{text[: match.end()]} ({expansion}){after}"
        return text


def _slug(value: str) -> str:
    """Turn free text into a filename-safe `Words_Joined_By_Underscores`.

    Args:
        value: Any text.

    Returns:
        ASCII letters and digits joined by single underscores.
    """
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", folded).strip("_")


def build_filename(
    identity: str, title: str, company: str | None, extension: str
) -> str:
    """Build `<surname>_<title>_<company>.<extension>`.

    Args:
        identity: The candidate's full name; the surname is its last word.
        title: The target job title (`title_for_display`).
        company: The employer, or None to leave it out.
        extension: File extension without the dot.

    Returns:
        A filename safe on any filesystem; empty parts are dropped.
    """
    surname = identity.split()[-1] if identity.split() else ""
    parts = [_slug(part) for part in (surname, title, company or "")]
    return "_".join(part for part in parts if part) + f".{extension}"
```

- [ ] **Step 4: Run to verify pass**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_format -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Format, lint, commit**

```bash
cd packages/core && arch -arm64 ../../venv/bin/black core/render tests/test_render_format.py && arch -arm64 ../../venv/bin/isort core/render tests/test_render_format.py && arch -arm64 ../../venv/bin/ruff check core/render tests/test_render_format.py && cd ../.. && git add packages/core/core/render packages/core/tests/test_render_format.py && git commit -m "feat(job_search): CV rendering text rules (dates, acronyms, filename)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Render model (section order, headings, content)

**Files:**
- Create: `packages/core/core/render/model.py`
- Create: `packages/core/tests/render_fixtures.py`
- Test: `packages/core/tests/test_render_model.py`

**Interfaces:**
- Consumes: Task 1's `format_date_range`, `AcronymExpander`; `core.tailoring.schema.TailoredDocument`.
- Produces:
  - `BlockKind = Literal["name", "headline", "contact", "heading", "role_title", "role_meta", "paragraph", "bullet"]`
  - `@dataclass(frozen=True) class Block: kind: BlockKind; text: str`
  - `@dataclass(frozen=True) class RenderDoc: title: str; blocks: tuple[Block, ...]` with `headings(self) -> list[str]`
  - `build_render_doc(doc: TailoredDocument) -> RenderDoc` (raises `ValueError` if `target_title` is blank)
  - Test helper `make_tailored_document(**overrides) -> TailoredDocument` in `tests/render_fixtures.py`.

- [ ] **Step 1: Write the fixture and failing tests**

Create `packages/core/tests/render_fixtures.py`:

```python
"""Shared invented fixture for the Step 18 rendering tests."""

from __future__ import annotations

from typing import Any

from core.cv.schema import Certification, Education, Skill
from core.tailoring.schema import (
    TailoredBullet,
    TailoredDocument,
    TailoredExperience,
    TailoredSummary,
)


def make_tailored_document(**overrides: Any) -> TailoredDocument:
    """Build a small invented `TailoredDocument`.

    Args:
        **overrides: Fields to replace on the default document.

    Returns:
        A document with a summary, two roles, skills, education and one
        certification. It names ELT and GCP in the summary and ELT and ETL
        in a bullet, to exercise first-use acronym expansion.
    """
    base = TailoredDocument(
        target_title="Lead Data Engineer",
        headline="Lead Data Engineer",
        identity="Zz Fixture",
        email="zz@example.com",
        phone="+00 000 000 000",
        linkedin_url="linkedin.com/in/zzfixture",
        nationality="Fixtureland",
        summary=TailoredSummary(
            text="Engineer building ELT pipelines on GCP.", origin="original"
        ),
        experience=[
            TailoredExperience(
                truth_index=0,
                company="Acme Bank",
                title="Senior Data Engineer",
                start="2019-01",
                end=None,
                bullets=[
                    TailoredBullet(
                        text="Built dbt models for risk reporting", origin="original"
                    ),
                    TailoredBullet(
                        text="Ran the ELT platform and wrote an ETL guide",
                        origin="reworded",
                        evidence_refs=["ref"],
                    ),
                ],
            ),
            TailoredExperience(
                truth_index=1,
                company="Beta Retail",
                title="Data Analyst",
                start="2015-06",
                end="2018-12",
                bullets=[TailoredBullet(text="Wrote SQL reports", origin="original")],
            ),
        ],
        skills=[Skill(name="dbt"), Skill(name="Airflow"), Skill(name="SQL")],
        education=[
            Education(
                institution="Zz University", qualification="BSc", start="2011", end="2014"
            )
        ],
        qualifications=[Certification(name="Fixture Cert", year=2020)],
    )
    return base.model_copy(update=overrides)
```

Create `packages/core/tests/test_render_model.py`:

```python
"""Unit tests for building the render model from a tailored document."""

from __future__ import annotations

import unittest

from tests.render_fixtures import make_tailored_document

from core.render.model import build_render_doc
from core.tailoring.schema import TailoredBullet, TailoredExperience, TailoredSummary


def _texts(doc, kind):  # type: ignore[no-untyped-def]
    return [b.text for b in doc.blocks if b.kind == kind]


class TestBuildRenderDoc(unittest.TestCase):
    def test_header_blocks_come_first_in_order(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertEqual(
            [b.kind for b in doc.blocks[:3]], ["name", "headline", "contact"]
        )
        self.assertEqual(doc.blocks[0].text, "Zz Fixture")
        self.assertEqual(doc.blocks[1].text, "Lead Data Engineer")
        self.assertEqual(
            doc.blocks[2].text,
            "zz@example.com | +00 000 000 000 | linkedin.com/in/zzfixture | Fixtureland",
        )

    def test_sections_use_the_standard_headings_in_order(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertEqual(
            doc.headings(),
            ["Summary", "Skills", "Experience", "Education", "Certifications"],
        )

    def test_roles_render_title_then_company_and_dates(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertEqual(
            _texts(doc, "role_title"), ["Senior Data Engineer", "Data Analyst"]
        )
        self.assertEqual(
            _texts(doc, "role_meta"),
            ["Acme Bank, 01/2019 – Present", "Beta Retail, 06/2015 – 12/2018"],
        )

    def test_skills_are_one_comma_separated_paragraph(self) -> None:
        doc = build_render_doc(make_tailored_document())
        self.assertIn("dbt, Airflow, SQL", _texts(doc, "paragraph"))

    def test_education_and_certification_lines(self) -> None:
        doc = build_render_doc(make_tailored_document())
        paragraphs = _texts(doc, "paragraph")
        self.assertIn("BSc, Zz University, 2011 – 2014", paragraphs)
        self.assertIn("Fixture Cert (2020)", paragraphs)

    def test_acronyms_expand_on_first_use_only_across_sections(self) -> None:
        doc = build_render_doc(make_tailored_document())
        summary = _texts(doc, "paragraph")[0]
        self.assertEqual(
            summary,
            "Engineer building ELT (Extract, Load, Transform) pipelines "
            "on GCP (Google Cloud Platform).",
        )
        self.assertIn(
            "Ran the ELT platform and wrote an ETL (Extract, Transform, Load) guide",
            _texts(doc, "bullet"),
        )

    def test_empty_optional_sections_have_no_heading(self) -> None:
        doc = build_render_doc(
            make_tailored_document(
                summary=None, education=[], qualifications=[], skills=[]
            )
        )
        self.assertEqual(doc.headings(), ["Experience"])

    def test_blank_target_title_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            build_render_doc(make_tailored_document(target_title="  "))

    def test_whitespace_in_text_is_collapsed(self) -> None:
        experience = [
            TailoredExperience(
                truth_index=0,
                company="Acme",
                title="Engineer",
                bullets=[
                    TailoredBullet(text="Built   dbt\nmodels\t", origin="original")
                ],
            )
        ]
        doc = build_render_doc(make_tailored_document(experience=experience))
        self.assertEqual(_texts(doc, "bullet"), ["Built dbt models"])

    def test_title_company_and_contact_are_never_expanded(self) -> None:
        experience = [
            TailoredExperience(
                truth_index=0, company="GCP Ltd", title="GCP Engineer", bullets=[]
            )
        ]
        doc = build_render_doc(
            make_tailored_document(
                target_title="ELT Engineer & Co",
                headline="ELT Engineer & Co",
                summary=TailoredSummary(text="Plain.", origin="original"),
                experience=experience,
            )
        )
        self.assertEqual(doc.blocks[1].text, "ELT Engineer & Co")
        self.assertEqual(_texts(doc, "role_title"), ["GCP Engineer"])
        self.assertEqual(_texts(doc, "role_meta"), ["GCP Ltd"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_model -v`
Expected: ERROR `ModuleNotFoundError: No module named 'core.render.model'`

- [ ] **Step 3: Implement**

Create `packages/core/core/render/model.py`:

```python
"""The rendering model: a TailoredDocument as a flat, ordered list of typed
blocks. This is the one place that fixes section order and heading names, so
the ATS .docx (18a) and the designed PDF (18b) cannot drift apart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.render.format import AcronymExpander, format_date_range
from core.tailoring.schema import TailoredDocument

BlockKind = Literal[
    "name",
    "headline",
    "contact",
    "heading",
    "role_title",
    "role_meta",
    "paragraph",
    "bullet",
]

_EXPANDED_KINDS = frozenset({"paragraph", "bullet"})


@dataclass(frozen=True)
class Block:
    """One line of the rendered document.

    Attributes:
        kind: What the line is, so a renderer can style it.
        text: The line's text, whitespace-collapsed.
    """

    kind: BlockKind
    text: str


@dataclass(frozen=True)
class RenderDoc:
    """A document ready to render.

    Attributes:
        title: The exact target job title (`title_for_display`).
        blocks: Every line, in reading order.
    """

    title: str
    blocks: tuple[Block, ...]

    def headings(self) -> list[str]:
        """List the section headings in order.

        Returns:
            The text of every `heading` block.
        """
        return [block.text for block in self.blocks if block.kind == "heading"]


def build_render_doc(doc: TailoredDocument) -> RenderDoc:
    """Lay a tailored document out as ordered blocks.

    Args:
        doc: The approved tailored document.

    Returns:
        The render model. Acronyms are expanded on first use in body
        paragraphs and bullets only; the name, headline, contact line and
        role lines are copied untouched.

    Raises:
        ValueError: If the target title is blank.
    """
    if not doc.target_title.strip():
        raise ValueError("a rendered CV needs a non-blank target title")
    expander = AcronymExpander()
    blocks: list[Block] = []

    def add(kind: BlockKind, text: str) -> None:
        """Append one block, collapsing whitespace and expanding acronyms.

        Args:
            kind: The block kind.
            text: Raw text; empty text adds nothing.
        """
        text = " ".join(text.split())
        if not text:
            return
        if kind in _EXPANDED_KINDS:
            text = expander.expand(text)
        blocks.append(Block(kind, text))

    def join(*parts: str | None, sep: str = ", ") -> str:
        """Join the non-empty parts.

        Args:
            *parts: Candidate parts, some possibly None or empty.
            sep: The separator.

        Returns:
            The non-empty parts joined by `sep`.
        """
        return sep.join(part for part in parts if part)

    add("name", doc.identity)
    add("headline", doc.target_title)
    add(
        "contact",
        join(
            doc.email,
            doc.phone,
            doc.linkedin_url,
            join(*doc.locations),
            doc.nationality,
            doc.work_auth,
            sep=" | ",
        ),
    )
    if doc.summary and doc.summary.text.strip():
        add("heading", "Summary")
        add("paragraph", doc.summary.text)
    if doc.skills:
        add("heading", "Skills")
        add("paragraph", join(*(skill.name for skill in doc.skills)))
    if doc.experience:
        add("heading", "Experience")
        for role in doc.experience:
            add("role_title", role.title)
            add("role_meta", join(role.company, format_date_range(role.start, role.end)))
            for bullet in role.bullets:
                add("bullet", bullet.text)
    if doc.education:
        add("heading", "Education")
        for edu in doc.education:
            add(
                "paragraph",
                join(
                    edu.qualification,
                    edu.institution,
                    format_date_range(edu.start, edu.end),
                    edu.grade,
                ),
            )
    if doc.projects:
        add("heading", "Projects")
        for project in doc.projects:
            add("paragraph", join(project.name, project.description, sep=": "))
    if doc.publications:
        add("heading", "Publications")
        for publication in doc.publications:
            add("paragraph", publication.citation)
    if doc.qualifications:
        add("heading", "Certifications")
        for cert in doc.qualifications:
            add("paragraph", f"{cert.name} ({cert.year})" if cert.year else cert.name)
    if doc.activities:
        add("heading", "Activities")
        for activity in doc.activities:
            add(
                "paragraph",
                join(
                    activity.name,
                    activity.organisation,
                    format_date_range(activity.start, activity.end),
                ),
            )
    return RenderDoc(title=doc.target_title, blocks=tuple(blocks))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_model tests.test_render_format -v`
Expected: PASS. If `test_education_and_certification_lines` fails on the date range for education with start/end only years, `format_date_range("2011","2014")` must give `2011 – 2014` (covered by Task 1).

- [ ] **Step 5: Format, lint, commit**

```bash
cd packages/core && arch -arm64 ../../venv/bin/black core/render tests/render_fixtures.py tests/test_render_model.py && arch -arm64 ../../venv/bin/isort core/render tests/render_fixtures.py tests/test_render_model.py && arch -arm64 ../../venv/bin/ruff check core/render tests/render_fixtures.py tests/test_render_model.py && cd ../.. && git add packages/core && git commit -m "feat(job_search): CV render model fixes section order and headings

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The ATS .docx writer and text extraction

**Files:**
- Create: `packages/core/core/render/docx_ats.py`
- Create: `packages/core/core/render/text.py`
- Test: `packages/core/tests/test_render_docx.py`

**Interfaces:**
- Consumes: Task 2's `RenderDoc`, `Block`, `build_render_doc`; `make_tailored_document`.
- Produces:
  - `write_docx(doc: RenderDoc, path: Path) -> None`
  - `render_text(doc: RenderDoc) -> str` (one block per line, trailing newline)
  - `extract_docx_text(path: Path) -> str` (every `w:p` in XML order, one per line, trailing newline)
  - `diff_texts(expected: str, actual: str) -> list[str]` (unified diff lines; empty when equal)

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_render_docx.py`:

```python
"""Unit tests for the ATS .docx writer and the text extraction."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from tests.render_fixtures import make_tailored_document

from core.render.docx_ats import write_docx
from core.render.model import build_render_doc
from core.render.text import diff_texts, extract_docx_text, render_text
from core.tailoring.schema import TailoredExperience

_FORBIDDEN_XML = ("<w:tbl", "txbxContent", "<w:drawing", "<w:pict")


class TestAtsDocx(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cv.docx"

    def _write(self, **overrides: object) -> None:
        write_docx(build_render_doc(make_tailored_document(**overrides)), self.path)

    def test_extracted_text_equals_the_text_twin(self) -> None:
        doc = build_render_doc(make_tailored_document())
        write_docx(doc, self.path)
        self.assertEqual(extract_docx_text(self.path), render_text(doc))

    def test_sections_read_in_the_intended_order(self) -> None:
        self._write()
        lines = extract_docx_text(self.path).splitlines()
        positions = [
            lines.index(h)
            for h in ("Summary", "Skills", "Experience", "Education", "Certifications")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_the_exact_title_is_in_the_rendered_text(self) -> None:
        title = "Lead Data Engineer – Données & Co"
        self._write(target_title=title, headline=title)
        self.assertIn(title, extract_docx_text(self.path).splitlines())

    def test_the_title_is_the_second_line_after_the_name(self) -> None:
        self._write()
        lines = extract_docx_text(self.path).splitlines()
        self.assertEqual(lines[:2], ["Zz Fixture", "Lead Data Engineer"])

    def test_no_tables_text_boxes_images_headers_or_footers(self) -> None:
        self._write()
        with zipfile.ZipFile(self.path) as package:
            names = package.namelist()
            body = package.read("word/document.xml").decode()
        for marker in _FORBIDDEN_XML:
            self.assertNotIn(marker, body)
        self.assertFalse([n for n in names if "header" in n or "footer" in n])
        self.assertFalse([n for n in names if n.startswith("word/media/")])

    def test_headings_and_bullets_use_real_styles(self) -> None:
        doc = build_render_doc(make_tailored_document())
        write_docx(doc, self.path)
        paragraphs = Document(str(self.path)).paragraphs
        headings = [p.text for p in paragraphs if p.style.name == "Heading 1"]
        bullets = [p.text for p in paragraphs if p.style.name == "List Bullet"]
        self.assertEqual(headings, doc.headings())
        self.assertEqual(
            bullets, [b.text for b in doc.blocks if b.kind == "bullet"]
        )

    def test_a_missing_company_and_dates_still_produce_a_valid_docx(self) -> None:
        role = TailoredExperience(truth_index=0, company="", title="Engineer")
        self._write(experience=[role])
        self.assertIn("Engineer", extract_docx_text(self.path).splitlines())


class TestDiffTexts(unittest.TestCase):
    def test_equal_texts_have_no_diff(self) -> None:
        self.assertEqual(diff_texts("a\nb\n", "a\nb\n"), [])

    def test_a_reordered_section_is_reported(self) -> None:
        diff = diff_texts("Skills\nExperience\n", "Experience\nSkills\n")
        self.assertTrue(diff)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_docx -v`
Expected: ERROR `ModuleNotFoundError: No module named 'core.render.docx_ats'`

- [ ] **Step 3: Implement**

Create `packages/core/core/render/text.py`:

```python
"""Plain-text views of a rendered CV: the `.txt` twin, and the text an ATS
would read out of the `.docx` (every paragraph in XML order)."""

from __future__ import annotations

import difflib
import zipfile
from pathlib import Path

from lxml import etree

from core.render.model import RenderDoc

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def render_text(doc: RenderDoc) -> str:
    """Render the document as plain text, one block per line.

    Args:
        doc: The render model.

    Returns:
        The text with a trailing newline.
    """
    return "\n".join(block.text for block in doc.blocks) + "\n"


def extract_docx_text(path: Path) -> str:
    """Read a `.docx` the way an ATS parser does: every paragraph, in the
    order it appears in the XML (not the visual order).

    Args:
        path: The `.docx` file.

    Returns:
        One paragraph per line with a trailing newline.
    """
    with zipfile.ZipFile(path) as package:
        root = etree.fromstring(package.read("word/document.xml"))
    lines = [
        "".join(node.text or "" for node in paragraph.iter(f"{{{_W}}}t"))
        for paragraph in root.iter(f"{{{_W}}}p")
    ]
    return "\n".join(lines) + "\n"


def diff_texts(expected: str, actual: str) -> list[str]:
    """Diff two texts line by line.

    Args:
        expected: The reference text.
        actual: The text to check.

    Returns:
        Unified-diff lines; empty when the texts are identical.
    """
    return list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile="txt",
            tofile="docx",
            lineterm="",
        )
    )
```

Create `packages/core/core/render/docx_ats.py`:

```python
"""The locked ATS .docx writer. It only ever adds plain paragraphs, headings
and list bullets, so tables, text boxes, headers/footers and images have no
code path: the ATS rules are enforced by construction, not by prompting."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType
from docx.shared import Mm, Pt, RGBColor

from core.render.model import Block, RenderDoc

_FONT = "Calibri"
_BODY_PT = 10.5


def _style_document(document: DocumentType) -> None:
    """Set A4 single-column page, margins and base fonts.

    Args:
        document: The new document to configure.
    """
    section = document.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Mm(18))
    normal = document.styles["Normal"]
    normal.font.name = _FONT
    normal.font.size = Pt(_BODY_PT)
    normal.paragraph_format.space_after = Pt(2)
    heading = document.styles["Heading 1"]
    heading.font.name = _FONT
    heading.font.size = Pt(12)
    heading.font.bold = True
    heading.font.color.rgb = RGBColor(0, 0, 0)
    heading.paragraph_format.space_before = Pt(10)
    heading.paragraph_format.space_after = Pt(3)


def _add_block(document: DocumentType, block: Block) -> None:
    """Append one block as a plain paragraph.

    Args:
        document: The document being built.
        block: The block to write.
    """
    if block.kind == "heading":
        document.add_paragraph(block.text, style="Heading 1")
        return
    if block.kind == "bullet":
        document.add_paragraph(block.text, style="List Bullet")
        return
    paragraph = document.add_paragraph()
    run = paragraph.add_run(block.text)
    if block.kind == "name":
        run.bold = True
        run.font.size = Pt(20)
    elif block.kind == "headline":
        run.bold = True
        run.font.size = Pt(12)
    elif block.kind == "role_title":
        run.bold = True
        paragraph.paragraph_format.space_before = Pt(6)
        paragraph.paragraph_format.keep_with_next = True
    elif block.kind == "role_meta":
        run.italic = True
        paragraph.paragraph_format.keep_with_next = True


def write_docx(doc: RenderDoc, path: Path) -> None:
    """Write the ATS-safe .docx.

    Args:
        doc: The render model.
        path: Where to save the file.
    """
    document = Document()
    _style_document(document)
    for block in doc.blocks:
        _add_block(document, block)
    document.core_properties.title = doc.title
    document.core_properties.author = ""
    document.save(str(path))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_docx tests.test_render_model tests.test_render_format -v`
Expected: PASS. If `test_no_tables_text_boxes_images_headers_or_footers` fails because the default python-docx template ships a `header`/`footer`/`media` part, inspect `package.namelist()` and either strip that part when saving or (if it is a harmless theme/thumbnail part) narrow the assertion to the parts that matter (`word/header*.xml`, `word/footer*.xml`, `word/media/*`), and say so in the commit message.

- [ ] **Step 5: Format, lint, commit**

```bash
cd packages/core && arch -arm64 ../../venv/bin/black core/render tests/test_render_docx.py && arch -arm64 ../../venv/bin/isort core/render tests/test_render_docx.py && arch -arm64 ../../venv/bin/ruff check core/render tests/test_render_docx.py && cd ../.. && git add packages/core && git commit -m "feat(job_search): locked ATS docx writer and XML-order text extraction

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Render service, `render-cv` CLI, docs and wiring

**Files:**
- Create: `packages/core/core/render/service.py`
- Modify: `apps/pipeline/app/cli.py` (imports near line 80; a new `_cmd_render_cv` after `_cmd_tailor_cv`; a `render-cv` subparser after the `tailor-cv` one; a dispatch line)
- Modify: `packages/core/tests/test_pipeline_registry.py:22` (add `"render-cv"` to `_EXCLUDED_FROM_RUN_BUTTON`)
- Modify: `requirements.txt` (add `python-docx==1.2.0`)
- Modify: `docker-compose.yml` (pipeline service volumes: add `./output:/app/output`)
- Modify: `README.md` (replace the closing sentence of "Tailored CV (Step 17)" and add a "Rendered CV (Step 18a)" section before "Further work")
- Test: `packages/core/tests/test_render_service.py`, `packages/core/tests/test_pipeline_cli_render.py`

**Interfaces:**
- Consumes: Tasks 1-3; `core.tailoring.store.latest_run_id(engine, user_id, job_group_id) -> UUID | None`, `read_run(engine, user_id, run_id) -> StoredRun | None` (`.status`, `.document`), `core.tailoring.context.load_job_context(engine, job_group_id) -> JobContext | None` (`.company`).
- Produces:
  - `class RenderError(Exception)`
  - `@dataclass(frozen=True) class RenderedCv: docx_path: Path; txt_path: Path`
  - `render_cv_files(tailored: TailoredDocument, company: str | None, out_dir: Path) -> RenderedCv`
  - CLI: `render-cv --user-id <uuid> --job-group-id <id> [--out-dir output]`

- [ ] **Step 1: Write the failing service tests**

Create `packages/core/tests/test_render_service.py`:

```python
"""Unit tests for the CV render service."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.render_fixtures import make_tailored_document

from core.render.service import RenderError, render_cv_files
from core.render.text import extract_docx_text


class TestRenderCvFiles(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "nested" / "out"

    def test_writes_a_docx_and_matching_txt_with_the_convention_name(self) -> None:
        files = render_cv_files(make_tailored_document(), "Acme Bank", self.out)
        self.assertEqual(
            files.docx_path.name, "Fixture_Lead_Data_Engineer_Acme_Bank.docx"
        )
        self.assertEqual(files.txt_path.suffix, ".txt")
        self.assertEqual(
            files.txt_path.read_text(encoding="utf-8"),
            extract_docx_text(files.docx_path),
        )

    def test_a_missing_company_is_left_out_of_the_filename(self) -> None:
        files = render_cv_files(make_tailored_document(), None, self.out)
        self.assertEqual(files.docx_path.name, "Fixture_Lead_Data_Engineer.docx")

    def test_a_parse_order_mismatch_fails_and_leaves_no_files(self) -> None:
        with mock.patch(
            "core.render.service.extract_docx_text", return_value="wrong\n"
        ):
            with self.assertRaises(RenderError):
                render_cv_files(make_tailored_document(), "Acme", self.out)
        self.assertEqual(list(self.out.glob("*")), [])

    def test_a_blank_title_is_refused_before_anything_is_written(self) -> None:
        with self.assertRaises(ValueError):
            render_cv_files(make_tailored_document(target_title=" "), "Acme", self.out)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_service -v`
Expected: ERROR `ModuleNotFoundError: No module named 'core.render.service'`

- [ ] **Step 3: Implement the service**

Create `packages/core/core/render/service.py`:

```python
"""Write the ATS .docx and .txt twin for one approved tailored CV, and verify
them against each other (Step 18a)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.render.docx_ats import write_docx
from core.render.format import build_filename
from core.render.model import build_render_doc
from core.render.text import diff_texts, extract_docx_text, render_text
from core.tailoring.schema import TailoredDocument


class RenderError(Exception):
    """The generated .docx does not read back as intended."""


@dataclass(frozen=True)
class RenderedCv:
    """The files written for one CV.

    Attributes:
        docx_path: The ATS .docx.
        txt_path: The plain-text twin it was verified against.
    """

    docx_path: Path
    txt_path: Path


def render_cv_files(
    tailored: TailoredDocument, company: str | None, out_dir: Path
) -> RenderedCv:
    """Render, write and verify the .docx and .txt for one CV.

    Args:
        tailored: The approved tailored document.
        company: The target employer for the filename, or None.
        out_dir: Directory to write into (created if missing).

    Returns:
        The two written paths.

    Raises:
        ValueError: If the target title is blank (nothing is written).
        RenderError: If the text extracted from the .docx differs from the
            .txt twin or lacks the exact target title; both files are
            removed.
    """
    render_doc = build_render_doc(tailored)
    out_dir.mkdir(parents=True, exist_ok=True)
    docx_path = out_dir / build_filename(
        tailored.identity, tailored.target_title, company, "docx"
    )
    txt_path = docx_path.with_suffix(".txt")
    expected = render_text(render_doc)
    write_docx(render_doc, docx_path)
    txt_path.write_text(expected, encoding="utf-8")
    extracted = extract_docx_text(docx_path)
    problems = diff_texts(expected, extracted)
    if tailored.target_title not in extracted.splitlines():
        problems.append("the exact target title is missing from the .docx text")
    if problems:
        docx_path.unlink(missing_ok=True)
        txt_path.unlink(missing_ok=True)
        raise RenderError(
            "the .docx does not read back as intended:\n" + "\n".join(problems[:20])
        )
    return RenderedCv(docx_path=docx_path, txt_path=txt_path)
```

- [ ] **Step 4: Run service tests to verify pass**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_service -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing CLI tests**

Create `packages/core/tests/test_pipeline_cli_render.py`:

```python
"""Unit tests for the `render-cv` pipeline CLI subcommand (database reads are
patched out; the render itself is real and writes to a temp directory)."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "pipeline"))

from app.cli import main  # noqa: E402
from tests.render_fixtures import make_tailored_document  # noqa: E402

_ARGS = ["--user-id", str(uuid.uuid4()), "--job-group-id", "j1"]


def _run(argv: list[str], run: object, company: str | None = "Acme Bank") -> tuple[int, str]:
    out = io.StringIO()
    with (
        mock.patch("app.cli.build_engine"),
        mock.patch("app.cli.latest_run_id", return_value=uuid.uuid4() if run else None),
        mock.patch("app.cli.read_run", return_value=run),
        mock.patch(
            "app.cli.load_job_context", return_value=SimpleNamespace(company=company)
        ),
        contextlib.redirect_stdout(out),
    ):
        return main(argv), out.getvalue()


class TestRenderCvSubcommand(unittest.TestCase):
    def test_is_registered(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["render-cv", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_run_for_the_job_exits_one(self) -> None:
        code, text = _run(["render-cv", *_ARGS], None)
        self.assertEqual(code, 1)
        self.assertIn("run tailor-cv first", text)

    def test_a_run_awaiting_review_is_not_rendered(self) -> None:
        run = SimpleNamespace(status="needs_review", document=make_tailored_document())
        code, text = _run(["render-cv", *_ARGS], run)
        self.assertEqual(code, 1)
        self.assertIn("needs_review", text)

    def test_an_approved_run_writes_both_files(self) -> None:
        run = SimpleNamespace(status="approved", document=make_tailored_document())
        with tempfile.TemporaryDirectory() as tmp:
            code, text = _run(["render-cv", *_ARGS, "--out-dir", tmp], run)
            names = sorted(p.name for p in Path(tmp).iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(
            names,
            [
                "Fixture_Lead_Data_Engineer_Acme_Bank.docx",
                "Fixture_Lead_Data_Engineer_Acme_Bank.txt",
            ],
        )
        self.assertIn("render-cv complete", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run to verify failure**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_pipeline_cli_render -v`
Expected: FAIL / SystemExit 2 (`invalid choice: 'render-cv'`) and `AttributeError` on `app.cli.latest_run_id`.

- [ ] **Step 7: Implement the CLI command and wiring**

In `apps/pipeline/app/cli.py`:

1. Add imports beside the existing `core.tailoring` imports (keep isort order):

```python
from core.render.service import RenderError, render_cv_files
from core.tailoring.context import load_job_context
from core.tailoring.store import latest_run_id, read_run
```
(replace the existing `from core.tailoring.store import read_run` line with the combined one above; `Path` is imported already if used elsewhere, otherwise add `from pathlib import Path`.)

2. Add after `_cmd_tailor_cv`:

```python
def _cmd_render_cv(args: argparse.Namespace) -> int:
    """Run the `render-cv` subcommand: write the ATS .docx and .txt for one
    approved tailored CV (Step 18a).

    On demand, not a batch stage, so it is deliberately absent from the
    pipeline dashboard (core.pipeline.registry).

    Args:
        args: Parsed CLI arguments — `user_id`, `job_group_id`, `out_dir`.

    Returns:
        0 when both files were written and verified; 1 when there is no
        approved run for the job or the .docx failed verification.
    """
    settings = get_settings()
    engine = build_engine(settings.app_database_url)
    run_id = latest_run_id(engine, args.user_id, args.job_group_id)
    run = read_run(engine, args.user_id, run_id) if run_id is not None else None
    if run is None:
        print("render-cv: no tailored CV for this job; run tailor-cv first")
        return 1
    if run.status != "approved" or run.document is None:
        print(
            f"render-cv: the latest run is {run.status}; "
            "only an approved run can be rendered"
        )
        return 1
    job = load_job_context(engine, args.job_group_id)
    try:
        files = render_cv_files(
            run.document, job.company if job else None, Path(args.out_dir)
        )
    except RenderError as exc:
        print(f"render-cv: {exc}")
        return 1
    print(f"render-cv complete: docx={files.docx_path} txt={files.txt_path}")
    return 0
```

3. Add the subparser right after the `tailor_cv_parser` arguments:

```python
    render_cv_parser = subparsers.add_parser(
        "render-cv",
        help="Write the ATS-safe .docx and .txt for one approved tailored CV "
        "(PLAN.md Step 18a); on demand, not a pipeline stage",
    )
    render_cv_parser.add_argument("--user-id", required=True, type=uuid.UUID)
    render_cv_parser.add_argument("--job-group-id", required=True)
    render_cv_parser.add_argument(
        "--out-dir",
        default="output",
        help="Where to write the files (default: ./output, git-ignored)",
    )
```

4. Add the dispatch next to the `tailor-cv` one:

```python
    if args.command == "render-cv":
        return _cmd_render_cv(args)
```

In `packages/core/tests/test_pipeline_registry.py` change the exclusion set and its comment:

```python
# tailor-cv and render-cv are on demand per (user, job) and are driven from
# the Tailored CV Review page / CLI, not the dashboard.
_EXCLUDED_FROM_RUN_BUTTON = {"ingest", "run-evals", "tailor-cv", "render-cv"}
```

In `requirements.txt` add after `pyyaml==6.0.2`:

```
python-docx==1.2.0
```

In `docker-compose.yml`, in the `pipeline` service `volumes:` list add:

```yaml
      - ./output:/app/output
```

- [ ] **Step 8: Update the README**

In `README.md`, in the "Tailored CV (Step 17)" section replace the sentence `This step produces approved *content* only. The ATS `.docx` and the designed PDF are Steps 18a and 18b.` with `This step produces approved *content* only. Rendering is Step 18.` Then add, immediately before `### Further work: the paid adversarial test`:

```markdown
## Rendered CV (Step 18a: ATS .docx)

`render-cv` writes an ATS-safe `.docx` and a plain-text twin for the latest
**approved** tailored CV of a job:

    docker compose run --rm pipeline render-cv --user-id <id> --job-group-id <job_group_id>

Files land in `./output/` (git-ignored; override with `--out-dir`) as
`<surname>_<title_for_display>_<company>.docx` and `.txt`. The writer only
ever adds plain paragraphs, so there are no tables, text boxes,
headers/footers or images. Headings are `Summary`, `Skills`, `Experience`,
`Education` (then `Projects`, `Publications`, `Certifications`,
`Activities` when present), dates read `MM/YYYY – MM/YYYY`, and a short list
of acronyms (ELT, ETL, GCP, AWS, CI/CD, API, NLP, ML) is expanded on first
use. After writing, the command re-reads the `.docx` in XML order and fails,
deleting both files, if its text differs from the `.txt` twin or lacks the
exact job title. The designed PDF is Step 18b.

Your CV and its renderings are personal data and this repo is public: never
commit anything from `output/` or `private/`.
```

- [ ] **Step 9: Run the whole affected suite**

Run: `cd packages/core && arch -arm64 ../../venv/bin/python -m unittest tests.test_render_format tests.test_render_model tests.test_render_docx tests.test_render_service tests.test_pipeline_cli_render tests.test_pipeline_registry tests.test_pipeline_cli_tailor -v`
Expected: all PASS.

Then confirm nothing personal is tracked: `git status --short` shows no files under `private/` or `output/`.

- [ ] **Step 10: Format, lint, commit**

```bash
cd packages/core && arch -arm64 ../../venv/bin/black core/render tests/test_render_service.py tests/test_pipeline_cli_render.py tests/test_pipeline_registry.py ../../apps/pipeline/app/cli.py && arch -arm64 ../../venv/bin/isort core/render tests/test_render_service.py tests/test_pipeline_cli_render.py ../../apps/pipeline/app/cli.py && arch -arm64 ../../venv/bin/ruff check core/render tests ../../apps/pipeline/app/cli.py && cd ../.. && git add -A packages apps requirements.txt docker-compose.yml README.md && git commit -m "feat(job_search): render-cv command writes and verifies the ATS docx and txt

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage:** shared render model (Task 2); `docx_ats`, `text` (Task 3); date/acronym/filename rules (Task 1); title injected, never transformed, asserted against rendered text (Tasks 2, 3, 4); no tables/text boxes/headers/footers/images asserted in the package (Task 3); `.txt` twin diffed against XML-order extraction (Tasks 3, 4); CLI entry point and company from `dim_company` via `load_job_context` (Task 4); private/output git-ignored (done earlier on this branch, verified Task 4 step 9). 18b is out of this plan by design.

**Placeholders:** none; two notes inside Tasks 2 and 3 correct slips in the test snippets and say exactly what to write.

**Type consistency:** `format_date_range`, `AcronymExpander.expand`, `build_filename(identity, title, company, extension)`, `build_render_doc(doc)`, `RenderDoc.headings()`, `write_docx(doc, path)`, `render_text`, `extract_docx_text`, `diff_texts`, `render_cv_files(tailored, company, out_dir)` are used with these exact names and signatures in every task.

**Review Focus:** each of the five lines has a test (Task 1: odd dates, acronym edge cases, filename characters; Task 2: whitespace, empty sections; Task 3: empty company/dates; Task 4: `None` company).
