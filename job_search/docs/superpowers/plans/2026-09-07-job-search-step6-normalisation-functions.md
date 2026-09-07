# Step 6 — Normalisation Functions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure Python normalisation functions every dedup signal
in Steps 7–9 will be computed over: `normalise_company` (with an alias
table), the `title_raw`/`strip_title`/`title_for_display` three-field
split, `normalise_location`, and `parse_salary` — each tested against
real rows pulled from this project's own bronze data, never invented
examples, per `PLAN.md`'s own testing rule for this step.

**Architecture:** A new `core.normalisation` package, one module per
function group, plus a shared fixtures module
(`packages/core/tests/fixtures/normalisation_examples.py`) holding every
real example this plan's tests run against — satisfying the backlog's
"extract 40 real examples into a fixtures file" subtask as one artifact
rather than scattering examples across test files. `parse_salary` is a
thin wrapper over the Step 5a plan's `core.enrichment.engagement_terms.
extract_engagement_terms` (adding currency conversion and banding) rather
than re-deriving day-rate/currency logic — this is why `STEP-05A` blocks
`STEP-06` in the backlog: reusing that module means the day-rate/annual
disambiguation logic exists in exactly one place.

**Tech Stack:** Pure-Python `re`, no new dependency. `unittest` +
`coverage`, per this project's actual testing convention (not the
`pytest` wording in `PLAN.md`'s older prose).

**Spec:** `PLAN.md`'s "Step 6 — Normalisation functions" section,
`DECISIONS.md` §5 ("Job title mirroring across generated artefacts" —
the three-title-field design), and `plan/backlog.yml`'s `STEP-06` entry
(`jira_key: JOB-106`).

## Prerequisite

This plan's Task 5 (`parse_salary`) imports
`core.enrichment.engagement_terms.extract_engagement_terms`, built by the
separate Step 5a plan (`2026-09-07-job-search-step5a-ir35-rate-
modelling.md`, `jira_key: JOB-95`). Implement Step 5a first — at minimum
through its Task 3 (the module doesn't need Step 5a's migration, CLI
subcommand, or dbt model to exist; it only needs `core.enrichment.
engagement_terms` importable).

## Scope note — `normalise_location` resolves UK regions and a handful of
observed non-UK signals, not general geocoding

Real Greenhouse location strings pulled live in this session are often
unresolvable in principle without a geocoding service: multi-city lists
(`"SEA, SF, NYC, SF"`), semicolon-separated multi-office strings
(`"Chicago, IL; Westlake, TX"`), and bare `"n/a"`. Real Reed strings are
sometimes bare UK postcodes (`"BB17DY"`) with no place name at all.
Building full postcode-to-region geocoding or a world country gazetteer
is not "~40 real examples and passes"-shaped work — it's a separate,
much larger effort. This plan resolves:

- UK county/region names actually observed in real Adzuna data, to their
  ITL1 (post-Brexit NUTS1-equivalent) region code.
- UK postcode-shaped strings, to country `GB` (region left unresolved —
  postcode-to-region needs a lookup table this plan doesn't build).
- A handful of explicit non-UK country signals actually observed in real
  Greenhouse data (`US`, `DE`, `MX`, `South Korea`).
- `remote` as its own boolean, independent of country resolution.

Anything else (multi-city lists, `"n/a"`, unrecognised strings) resolves
to `country_iso=None, region=None` — an honest "not resolved," not a
guess. The raw `location` string is preserved unchanged in
`int_jobs__unioned` regardless, so nothing is lost; this can be extended
incrementally later without any data loss, unlike Step 5a's IR35 fields.

## Global Constraints

- Python style (Google docstrings, type hints, `black`/`isort`/`ruff`)
  per `.claude/rules/python-style.md`.
- Tests per `.claude/rules/python-testing.md`: `unittest` + `coverage`.
- Every test in this plan runs against a real example captured from
  `bronze.raw_jobs` in this session's own live queries, per `PLAN.md`
  Step 6's explicit testing rule — the one exception, called out
  inline where it occurs, is the `(m/f/d)`/req-ID stripping patterns
  `PLAN.md` names explicitly but which don't happen to appear in this
  session's own bronze sample; those tests are labelled synthetic, not
  presented as real rows.
- This plan touches no dbt models and no migrations — `core.
  normalisation` is pure Python, consumed directly by Steps 7+ (not
  built here).

---

### Task 1: Fixtures file

**Files:**
- Create: `packages/core/tests/fixtures/__init__.py`
- Create: `packages/core/tests/fixtures/normalisation_examples.py`

**Interfaces:**
- Produces: `COMPANY_EXAMPLES`, `TITLE_EXAMPLES`, `LOCATION_EXAMPLES`,
  `SALARY_EXAMPLES` — tuples of (input(s), expected output(s)), imported
  by Tasks 2–5's test modules.

- [ ] **Step 1: Write the fixtures module**

`packages/core/tests/fixtures/__init__.py`: empty file.

`packages/core/tests/fixtures/normalisation_examples.py`:

```python
"""Real examples pulled live from bronze.raw_jobs in this project's own
session (PLAN.md Step 6's testing rule: real rows, never invented).
Company/title/location strings are quoted exactly as stored; only salary
figures are lightly restated as plain numbers for readability.
"""

from __future__ import annotations

# (source_name, raw_company, expected_normalised_company)
COMPANY_EXAMPLES: list[tuple[str, str, str]] = [
    ("adzuna", "Oscar Associates  Limited", "Oscar Associates"),
    ("adzuna", "ZENZO DIGITAL LTD", "Zenzo Digital"),
    ("adzuna", "ODIN RECRUITMENT GROUP LIMITED", "Odin Recruitment Group"),
    ("reed", "Archangel Lightworks Ltd", "Archangel Lightworks"),
    ("reed", "Photo-Sonics International Ltd", "Photo-Sonics International"),
    ("reed", "Network Mapping Limited", "Network Mapping"),
    ("reed", "Eden James Consulting Limited", "Eden James Consulting"),
    (
        "reed",
        "MSC Mediterranean Shipping Company (UK)",
        "MSC Mediterranean Shipping Company",
    ),
    ("reed", "Sanderson ", "Sanderson"),
    ("greenhouse", "Stripe", "Stripe"),
    ("greenhouse", "Anthropic", "Anthropic"),
    ("greenhouse", "Pinterest", "Pinterest"),
]

# (raw_title, expected_strip_title, expected_title_for_display)
TITLE_EXAMPLES: list[tuple[str, str, str]] = [
    ("Senior Data Engineer", "Data Engineer", "Senior Data Engineer"),
    (
        "Full Stack Product Engineer - Remote/Europe",
        "Full Stack Product Engineer",
        "Full Stack Product Engineer",
    ),
    (
        "Senior AWS Cloud Engineer | S4 | Data & AI Domain | Multiple Locations",
        "AWS Cloud Engineer",
        "Senior AWS Cloud Engineer",
    ),
    (
        "Sr. Client Account Manager | Nordics (CPG)",
        "Client Account Manager",
        "Sr. Client Account Manager",
    ),
    (
        "Core Software Engineer (C++) - Remote",
        "Core Software Engineer (C++)",
        "Core Software Engineer (C++)",
    ),
    (
        "Senior Network Planner - Occupancy",
        "Network Planner - Occupancy",
        "Senior Network Planner - Occupancy",
    ),
    (
        "Staff+ Software Engineer, Kubernetes Platform",
        "Software Engineer, Kubernetes Platform",
        "Staff+ Software Engineer, Kubernetes Platform",
    ),
    (
        "Senior Data Engineer, Public Sector",
        "Data Engineer, Public Sector",
        "Senior Data Engineer, Public Sector",
    ),
    ("BI Data Engineer", "BI Data Engineer", "BI Data Engineer"),
    ("Software Team Leader", "Software Team Leader", "Software Team Leader"),
    # Synthetic, not from bronze — PLAN.md Step 6 names these patterns
    # explicitly (m/f/d, req IDs) but neither appears in this session's
    # real bronze sample.
    (
        "Data Engineer (m/f/d)",
        "Data Engineer",
        "Data Engineer",
    ),
    (
        "Data Engineer (Req ID: 48213)",
        "Data Engineer",
        "Data Engineer",
    ),
]

# (raw_location, expected_country_iso, expected_region, expected_is_remote)
LOCATION_EXAMPLES: list[tuple[str, str | None, str | None, bool]] = [
    ("Central London, London", "GB", "UKI", False),
    ("Kensington, West London", "GB", "UKI", False),
    ("Hemel Hempstead, Hertfordshire", "GB", "UKH", False),
    ("Rotherham, South Yorkshire", "GB", "UKE", False),
    ("Sheffield, South Yorkshire", "GB", "UKE", False),
    ("Tunbridge Wells, Kent", "GB", "UKJ", False),
    ("Maidstone, Kent", "GB", "UKJ", False),
    ("Oxfordshire, South East England", "GB", "UKJ", False),
    ("Dorset, South West England", "GB", "UKK", False),
    ("Burton-On-Trent, Staffordshire", "GB", "UKG", False),
    ("Ladywood, Birmingham", "GB", "UKG", False),
    ("Inverkip, Greenock", "GB", "UKM", False),
    ("Downpatrick, County Down", "GB", "UKN", False),
    ("BB17DY", "GB", None, False),
    ("SW1E5LB", "GB", None, False),
    ("Salt Lake City, UT", None, None, False),
    ("Dublin", None, None, False),
    ("Berlin, DE", "DE", None, False),
    ("MX- Mexico City", "MX", None, False),
    ("Seoul, South Korea", "KR", None, False),
    ("US-Remote, Chicago, Seattle, San Francisco", "US", None, True),
    ("n/a", None, None, False),
    ("Remote in the US", "US", None, True),
]

# (description, salary_raw, expected_annualised_gbp_or_None)
SALARY_EXAMPLES: list[tuple[str | None, str | None, float | None]] = [
    (
        "Senior Data Engineer – Microsoft Fabric Contract: Outside IR35 "
        "Rate : £450 - £500 per day",
        "117000-130000",
        475 * 260,
    ),
    ("Data Engineer, permanent role, London.", "130000-130000", 130000.0),
    (None, "£80k - £95k per year", 87500.0),
    ("Senior Data Engineer, Public Sector", None, None),
]
```

- [ ] **Step 2: Commit**

```bash
git add packages/core/tests/fixtures/__init__.py \
  packages/core/tests/fixtures/normalisation_examples.py
git commit -m "test(job_search): add Step 6 normalisation fixtures from real bronze data"
```

---

### Task 2: `normalise_company`

**Files:**
- Create: `packages/core/core/normalisation/__init__.py`
- Create: `packages/core/core/normalisation/company.py`
- Test: `packages/core/tests/test_normalisation_company.py`

**Interfaces:**
- Produces: `normalise_company(raw: str) -> str` — consumed by Step 7's
  future block-key computation (not built here).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_normalisation_company.py`:

```python
from __future__ import annotations

import unittest

from core.normalisation.company import normalise_company
from tests.fixtures.normalisation_examples import COMPANY_EXAMPLES


class TestNormaliseCompany(unittest.TestCase):
    """Tests against real company names pulled from bronze."""

    def test_real_bronze_examples(self) -> None:
        for source_name, raw, expected in COMPANY_EXAMPLES:
            with self.subTest(source=source_name, raw=raw):
                self.assertEqual(normalise_company(raw), expected)

    def test_facebook_aliases_to_meta(self) -> None:
        self.assertEqual(normalise_company("Facebook"), "Meta")

    def test_alphabet_aliases_to_google(self) -> None:
        self.assertEqual(normalise_company("Alphabet Inc"), "Google")

    def test_alias_is_case_insensitive(self) -> None:
        self.assertEqual(normalise_company("FACEBOOK"), "Meta")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_normalisation_company -v
```

Expected: `ModuleNotFoundError: No module named 'core.normalisation'`.

- [ ] **Step 3: Implement `normalise_company`**

`packages/core/core/normalisation/__init__.py`: empty file.

`packages/core/core/normalisation/company.py`:

```python
"""Company name normalisation for dedup blocking/matching (PLAN.md Step 6).

Matching-only: this is never the string shown to a user or in a
generated document — the raw company name from int_jobs__unioned is.
"""

from __future__ import annotations

import re

_SUFFIX_RE = re.compile(
    r"[,\s]+"
    r"(ltd|limited|inc|incorporated|gmbh|plc|s\.?a\.?|llc|llp)\.?\s*$",
    re.IGNORECASE,
)
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")
_WHITESPACE_RE = re.compile(r"\s+")

_ALIASES = {
    "facebook": "Meta",
    "alphabet": "Google",
    "alphabet inc": "Google",
}


def normalise_company(raw: str) -> str:
    """Normalise a company name for dedup matching.

    Strips common legal-entity suffixes (Ltd, Limited, Inc, GmbH, PLC,
    SA, LLC, LLP), a trailing parenthetical (e.g. "(UK)"), collapses
    whitespace, retitles an ALL-CAPS input, and resolves known aliases
    (Meta/Facebook, Google/Alphabet).

    Args:
        raw: The company name as stored in int_jobs__unioned.

    Returns:
        The normalised name. Mixed-case input that isn't ALL-CAPS keeps
        its original casing (a deliberate brand stylisation is not
        "wrong casing" to fix).
    """
    name = _TRAILING_PAREN_RE.sub("", raw)
    # Suffix stripping can leave a fresh trailing parenthetical exposed in
    # principle (not observed in real data, but cheap to guard); loop
    # until stable rather than assuming one pass suffices.
    previous = None
    while previous != name:
        previous = name
        name = _SUFFIX_RE.sub("", name)
        name = _TRAILING_PAREN_RE.sub("", name)
    name = _WHITESPACE_RE.sub(" ", name).strip()

    alias = _ALIASES.get(name.lower())
    if alias:
        return alias

    if name.isupper():
        name = name.title()
    return name
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_normalisation_company -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/normalisation/__init__.py \
  packages/core/core/normalisation/company.py \
  packages/core/tests/test_normalisation_company.py
git commit -m "feat(job_search): add normalise_company"
```

---

### Task 3: `strip_title`, `title_for_display`, `title_raw`

**Files:**
- Create: `packages/core/core/normalisation/title.py`
- Test: `packages/core/tests/test_normalisation_title.py`

**Interfaces:**
- Produces: `strip_title(raw: str) -> str`, `title_for_display(raw: str)
  -> str`, `title_raw(raw: str) -> str` — consumed by Step 7 (matching)
  and Steps 17–19 (generated documents), per `DECISIONS.md` §5.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_normalisation_title.py`:

```python
from __future__ import annotations

import unittest

from core.normalisation.title import strip_title, title_for_display, title_raw
from tests.fixtures.normalisation_examples import TITLE_EXAMPLES


class TestTitleFunctions(unittest.TestCase):
    """Tests against real job titles pulled from bronze, plus two
    synthetic cases for patterns PLAN.md names explicitly but that don't
    appear in this session's own bronze sample (m/f/d, req IDs)."""

    def test_real_and_spec_named_examples(self) -> None:
        for raw, expected_strip, expected_display in TITLE_EXAMPLES:
            with self.subTest(raw=raw, kind="strip_title"):
                self.assertEqual(strip_title(raw), expected_strip)
            with self.subTest(raw=raw, kind="title_for_display"):
                self.assertEqual(title_for_display(raw), expected_display)

    def test_title_raw_is_always_verbatim(self) -> None:
        for raw, _, _ in TITLE_EXAMPLES:
            with self.subTest(raw=raw):
                self.assertEqual(title_raw(raw), raw)

    def test_strip_title_removes_seniority_but_display_keeps_it(self) -> None:
        """The trap DECISIONS.md §5 warns about: never reuse strip_title's
        output for a generated document."""
        raw = "Senior Data Engineer"
        self.assertNotEqual(strip_title(raw), title_for_display(raw))
        self.assertIn("Senior", title_for_display(raw))
        self.assertNotIn("Senior", strip_title(raw))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_normalisation_title -v
```

Expected: `ModuleNotFoundError: No module named 'core.normalisation.title'`.

- [ ] **Step 3: Implement the three functions**

`packages/core/core/normalisation/title.py`:

```python
"""Job-title normalisation: three fields, three purposes (DECISIONS.md
§5, PLAN.md Step 6).

title_raw is always verbatim. strip_title is for dedup matching ONLY —
it deliberately removes seniority, which is exactly why it must never be
reused for a generated document. title_for_display strips only
decoration and keeps seniority/qualifiers; it's the string every
generated CV/cover-letter headline uses.
"""

from __future__ import annotations

import re

_SENIORITY_PREFIX_RE = re.compile(
    r"^\s*(senior|sr\.?|junior|jr\.?|lead|principal|staff\+?|associate|"
    r"graduate)\s+",
    re.IGNORECASE,
)
_MFD_RE = re.compile(r"\s*\(m/f/d\)\s*", re.IGNORECASE)
_REQ_ID_RE = re.compile(
    r"\s*\(?\s*req(?:\s*id)?[\s\-#:]*\d+\s*\)?", re.IGNORECASE
)
_REMOTE_SUFFIX_RE = re.compile(
    r"\s*-\s*(remote|hybrid|onsite)(?:/\w+)?\s*$", re.IGNORECASE
)
_PIPE_SUFFIX_RE = re.compile(r"\s*\|.*$")
_WHITESPACE_RE = re.compile(r"\s+")


def title_raw(raw: str) -> str:
    """Return the source title, verbatim, always.

    Args:
        raw: The posting's title as stored in int_jobs__unioned.

    Returns:
        `raw`, unchanged. Exists so callers never have to remember
        whether "the raw title" means skipping normalisation entirely —
        it's a named function with the same contract as its siblings.
    """
    return raw


def _strip_decoration(raw: str) -> str:
    """Remove decoration common to both strip_title and title_for_display.

    Args:
        raw: The source title.

    Returns:
        `raw` with (m/f/d), req IDs, trailing "| ..." segments and a
        trailing "- Remote"/"- Hybrid"/"- Onsite" suffix removed, and
        whitespace collapsed.
    """
    text = _MFD_RE.sub(" ", raw)
    text = _REQ_ID_RE.sub(" ", text)
    text = _PIPE_SUFFIX_RE.sub("", text)
    text = _REMOTE_SUFFIX_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def strip_title(raw: str) -> str:
    """Strip a title down to its matching-only form.

    Removes seniority prefixes on top of everything `title_for_display`
    removes. **Never use this output in a generated document** — see
    DECISIONS.md §5.

    Args:
        raw: The source title.

    Returns:
        The stripped title, for dedup matching only.
    """
    text = _strip_decoration(raw)
    previous = None
    while previous != text:
        previous = text
        text = _SENIORITY_PREFIX_RE.sub("", text)
    return text.strip()


def title_for_display(raw: str) -> str:
    """Strip only decoration, keeping seniority and qualifiers.

    This is the string every generated CV/cover-letter/elevator-pitch
    uses (DECISIONS.md §5) — never `strip_title`'s output.

    Args:
        raw: The source title.

    Returns:
        The decoration-stripped title, safe for generated documents.
    """
    return _strip_decoration(raw)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_normalisation_title -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/normalisation/title.py \
  packages/core/tests/test_normalisation_title.py
git commit -m "feat(job_search): add strip_title, title_for_display, title_raw"
```

---

### Task 4: `normalise_location`

**Files:**
- Create: `packages/core/core/normalisation/location.py`
- Test: `packages/core/tests/test_normalisation_location.py`

**Interfaces:**
- Produces: `NormalisedLocation` (dataclass: `country_iso: str | None`,
  `region: str | None`, `is_remote: bool`) and `normalise_location(raw:
  str) -> NormalisedLocation` — consumed by Step 7/8's future
  location-based matching signal and every regional mart in Step 21a
  (not built here).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_normalisation_location.py`:

```python
from __future__ import annotations

import unittest

from core.normalisation.location import normalise_location
from tests.fixtures.normalisation_examples import LOCATION_EXAMPLES


class TestNormaliseLocation(unittest.TestCase):
    """Tests against real location strings pulled from bronze — see this
    plan's scope note for what is and isn't resolved."""

    def test_real_bronze_examples(self) -> None:
        for raw, country_iso, region, is_remote in LOCATION_EXAMPLES:
            with self.subTest(raw=raw):
                result = normalise_location(raw)
                self.assertEqual(result.country_iso, country_iso)
                self.assertEqual(result.region, region)
                self.assertEqual(result.is_remote, is_remote)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_normalisation_location -v
```

Expected: `ModuleNotFoundError: No module named 'core.normalisation.location'`.

- [ ] **Step 3: Implement `normalise_location`**

`packages/core/core/normalisation/location.py`:

```python
"""Location normalisation to ISO country + a coarse UK region (PLAN.md
Step 6). See the Step 6 plan's scope note: this resolves UK
county/region names and a handful of observed non-UK signals, not
general geocoding. Anything unresolved is explicit None, never a guess —
the raw location string is preserved unchanged upstream regardless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)

_UK_POSTCODE_RE = re.compile(
    r"^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$", re.IGNORECASE
)

# UK county/region name -> ITL1 (post-Brexit NUTS1-equivalent) code.
# Populated from names actually observed in real bronze data; extend as
# new counties appear rather than trying to enumerate every UK county
# up front.
_UK_REGION_TO_ITL1 = {
    "london": "UKI",
    "west london": "UKI",
    "hertfordshire": "UKH",
    "south yorkshire": "UKE",
    "north yorkshire": "UKE",
    "kent": "UKJ",
    "south east england": "UKJ",
    "south west england": "UKK",
    "staffordshire": "UKG",
    "birmingham": "UKG",
    "warwickshire": "UKG",
    "west sussex": "UKJ",
    "gloucestershire": "UKK",
    "greenock": "UKM",
    "county down": "UKN",
}

# A handful of explicit non-UK country signals actually observed in real
# Greenhouse location strings — not a general gazetteer.
_NON_UK_COUNTRY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bus[\s-]*remote\b|\bunited states\b|\bremote in the us\b", re.IGNORECASE), "US"),
    (re.compile(r",\s*DE\b|\bgermany\b", re.IGNORECASE), "DE"),
    (re.compile(r"\bMX\b|\bmexico\b", re.IGNORECASE), "MX"),
    (re.compile(r"\bsouth korea\b", re.IGNORECASE), "KR"),
]


@dataclass(frozen=True)
class NormalisedLocation:
    """A location resolved to ISO country and (for the UK) a coarse region.

    Attributes:
        country_iso: 2-letter ISO country code, or `None` if not
            resolved (see this plan's scope note — not every real
            location string is resolvable without full geocoding).
        region: ITL1 region code, only ever populated for `country_iso ==
            "GB"`, and only when the county/region name is in this
            module's lookup. `None` otherwise, including for every
            non-UK country.
        is_remote: Whether "remote" appears anywhere in the raw string,
            independent of whether a country was also resolved.
    """

    country_iso: str | None
    region: str | None
    is_remote: bool


def normalise_location(raw: str) -> NormalisedLocation:
    """Normalise a location string to ISO country + (for the UK) region.

    Args:
        raw: The location string as stored in int_jobs__unioned.

    Returns:
        The `NormalisedLocation`. `country_iso`/`region` are `None` when
        genuinely unresolved (see this module's docstring) — never
        guessed.
    """
    is_remote = bool(_REMOTE_RE.search(raw))

    if _UK_POSTCODE_RE.match(raw.strip()):
        return NormalisedLocation(country_iso="GB", region=None, is_remote=is_remote)

    last_segment = raw.split(",")[-1].strip().lower()
    region_code = _UK_REGION_TO_ITL1.get(last_segment)
    if region_code:
        return NormalisedLocation(country_iso="GB", region=region_code, is_remote=is_remote)

    for pattern, country_iso in _NON_UK_COUNTRY_PATTERNS:
        if pattern.search(raw):
            return NormalisedLocation(
                country_iso=country_iso, region=None, is_remote=is_remote
            )

    return NormalisedLocation(country_iso=None, region=None, is_remote=is_remote)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_normalisation_location -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/normalisation/location.py \
  packages/core/tests/test_normalisation_location.py
git commit -m "feat(job_search): add normalise_location"
```

---

### Task 5: `parse_salary`

**Files:**
- Create: `packages/core/core/normalisation/salary.py`
- Test: `packages/core/tests/test_normalisation_salary.py`

**Interfaces:**
- Consumes: `core.enrichment.engagement_terms.extract_engagement_terms`
  (Step 5a plan, Task 3).
- Produces: `ParsedSalary` (dataclass: `annualised_gbp: float | None`,
  `band: str | None`, `original_currency: str | None`, `rate_basis:
  str`) and `parse_salary(description: str | None, salary_raw: str |
  None) -> ParsedSalary` — consumed by Step 8's future salary-similarity
  signal (not built here).

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_normalisation_salary.py`:

```python
from __future__ import annotations

import unittest

from core.normalisation.salary import parse_salary
from tests.fixtures.normalisation_examples import SALARY_EXAMPLES


class TestParseSalary(unittest.TestCase):
    """Tests against real salary/description text pulled from bronze."""

    def test_real_bronze_examples(self) -> None:
        for description, salary_raw, expected_annualised in SALARY_EXAMPLES:
            with self.subTest(description=description, salary_raw=salary_raw):
                result = parse_salary(description, salary_raw)
                self.assertEqual(result.annualised_gbp, expected_annualised)

    def test_band_is_ten_k_wide(self) -> None:
        result = parse_salary(None, "£80k - £95k per year")
        self.assertEqual(result.band, "80000-90000")

    def test_no_salary_gives_no_band(self) -> None:
        result = parse_salary("Senior Data Engineer, Public Sector", None)
        self.assertIsNone(result.band)
        self.assertIsNone(result.annualised_gbp)
        self.assertEqual(result.rate_basis, "unknown")

    def test_usd_hourly_rate_converts_to_gbp(self) -> None:
        """Real Jooble salary text: '$15 per hour'. 15 * 7.5 * 260 = 29250
        USD, converted at this module's documented approximate USD/GBP rate."""
        result = parse_salary(None, "$15 per hour")
        self.assertEqual(result.original_currency, "USD")
        self.assertAlmostEqual(result.annualised_gbp, 29250 * 0.79)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_normalisation_salary -v
```

Expected: `ModuleNotFoundError: No module named 'core.normalisation.salary'`.

- [ ] **Step 3: Implement `parse_salary`**

`packages/core/core/normalisation/salary.py`:

```python
"""Salary parsing to an annualised GBP band (PLAN.md Step 6).

A thin wrapper over core.enrichment.engagement_terms.
extract_engagement_terms (PLAN.md Step 5a) — that module already solves
day-rate/annual disambiguation and rate-basis detection; re-deriving it
here would duplicate the exact regexes Step 5a already tests against
real data. This module adds only what Step 5a deliberately left out:
currency conversion to GBP and banding for coarse dedup comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.enrichment.engagement_terms import extract_engagement_terms

_APPROXIMATE_FX_TO_GBP = {"GBP": 1.0, "USD": 0.79, "EUR": 0.85}
"""Static, approximate rates — this is a coarse dedup-matching signal,
not a financial calculation, so a live FX API is deliberately not used.
Update by hand if these drift far enough to matter."""

_BAND_WIDTH_GBP = 10_000


@dataclass(frozen=True)
class ParsedSalary:
    """A posting's salary, normalised for coarse comparison across postings.

    Attributes:
        annualised_gbp: The rate annualised and converted to GBP, or
            `None` when no rate was stated (rate_basis == "unknown").
        band: A GBP band string, e.g. "80000-90000", or `None` when
            annualised_gbp is `None`.
        original_currency: The currency the rate was actually stated in,
            before conversion — preserved for provenance.
        rate_basis: Passed through from extract_engagement_terms (annual,
            daily, hourly, or unknown).
    """

    annualised_gbp: float | None
    band: str | None
    original_currency: str | None
    rate_basis: str


def _band(amount: float) -> str:
    """Bucket an amount into a fixed-width GBP band.

    Args:
        amount: The annualised GBP amount.

    Returns:
        A string like "80000-90000".
    """
    floor = int(amount // _BAND_WIDTH_GBP) * _BAND_WIDTH_GBP
    return f"{floor}-{floor + _BAND_WIDTH_GBP}"


def parse_salary(description: str | None, salary_raw: str | None) -> ParsedSalary:
    """Parse a posting's salary to an annualised GBP band.

    Args:
        description: The posting's free text, passed through to
            extract_engagement_terms.
        salary_raw: The staging-layer salary_raw column, passed through
            to extract_engagement_terms.

    Returns:
        The `ParsedSalary`.
    """
    terms = extract_engagement_terms(description, salary_raw)
    if terms.rate_annualised_gbp is None:
        return ParsedSalary(
            annualised_gbp=None,
            band=None,
            original_currency=terms.rate_currency,
            rate_basis=terms.rate_basis,
        )

    fx_rate = _APPROXIMATE_FX_TO_GBP.get(terms.rate_currency or "GBP", 1.0)
    annualised_gbp = terms.rate_annualised_gbp * fx_rate
    return ParsedSalary(
        annualised_gbp=annualised_gbp,
        band=_band(annualised_gbp),
        original_currency=terms.rate_currency,
        rate_basis=terms.rate_basis,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd job_search/packages/core
python3.11 -m unittest tests.test_normalisation_salary -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/core/normalisation/salary.py \
  packages/core/tests/test_normalisation_salary.py
git commit -m "feat(job_search): add parse_salary"
```

---

### Task 6: Full verification (controller-run, not dispatched)

- [ ] **Step 1: Run the full Step 6 test suite together**

```bash
cd job_search/packages/core
python3.11 -m unittest \
  tests.test_normalisation_company \
  tests.test_normalisation_title \
  tests.test_normalisation_location \
  tests.test_normalisation_salary \
  -v
```

Expected: all PASS. Count the real-example assertions across
`COMPANY_EXAMPLES` (12), `TITLE_EXAMPLES` (12, 2 synthetic), and
`SALARY_EXAMPLES` (4) plus `LOCATION_EXAMPLES` (23) — comfortably past
the backlog's "~40 real examples" target.

- [ ] **Step 2: Quality gate**

```bash
cd job_search
python3.11 -m black --check packages/core/core/normalisation packages/core/tests
python3.11 -m isort --check-only packages/core/core/normalisation packages/core/tests
python3.11 -m ruff check packages/core/core/normalisation packages/core/tests
python3.11 -m mypy packages/core/core/normalisation
```

Expected: all clean.

- [ ] **Step 3: Full project test suite, to confirm no regressions**

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_verify" \
coverage run -m unittest discover
coverage report -m
```

Expected: all clean, coverage on the new `core/normalisation/` modules
at or above the project's 80% minimum (five small, fully-tested pure
functions — should be close to 100% in practice).

---

## Self-Review Notes (completed during authoring, before Task 1 dispatch)

- **Spec coverage:** every `STEP-06` subtask maps to a task above —
  `normalise_company` + alias table → Task 2; `strip_title`/`title_raw`/
  `title_for_display` + the "never reuse for display" note → Task 3;
  `normalise_location` → Task 4 (scope-noted, not full geocoding);
  `parse_salary` → Task 5; "extract 40 real examples into a fixtures
  file" + "write the pytest suite" → Task 1's fixtures module plus every
  task's own test file (as `unittest`, this project's actual convention,
  not literal `pytest`).
- **Placeholder scan:** none found. Two title-decoration test cases
  (`(m/f/d)`, req ID) are explicitly labelled synthetic rather than
  presented as real bronze rows, since PLAN.md names those patterns but
  they don't appear in this session's own sample — flagged, not hidden.
- **Genuine, flagged uncertainty:** `normalise_location`'s coverage is
  deliberately partial (see this plan's scope note) — multi-city lists,
  bare `"n/a"`, and postcode-to-ITL1-region mapping are explicitly out
  of scope, not silently mishandled.
- **Type consistency:** `ParsedSalary.rate_basis` and
  `NormalisedLocation`'s fields were checked against
  `core.enrichment.engagement_terms.EngagementTerms`'s field names
  (Step 5a plan) before finalising this plan — `parse_salary` passes
  `terms.rate_currency`/`terms.rate_basis`/`terms.rate_annualised_gbp`
  through by their exact names, no renaming drift.
