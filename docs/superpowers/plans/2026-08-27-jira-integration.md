# Jira Sync Kit + job_search Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the reusable `jira_sync_kit` package (Jira Cloud REST client, one-way backlog sync, ad-hoc issue-creation CLI) and adopt it in `claude_project_template` and `job_search`, per the design spec.

**Architecture:** `jira_sync_kit` is a standalone installable package (already scaffolded at [github.com/FredGH/jira_sync_kit](https://github.com/FredGH/jira_sync_kit), private) containing all Jira REST logic. `claude_project_template/` gets only the Claude-Code-specific glue (a drafting skill, a conventions rule) that has no packaging equivalent. `job_search` installs the package and supplies its own `backlog.yml` and credentials. Phase A builds the package; Phase B wires it into the template and job_search. They're kept in one plan (not split per the writing-plans default) because Phase B's tasks consume exact CLI/function signatures Phase A defines — splitting them would mean guessing those signatures twice.

**Tech Stack:** Python 3.11, `requests` against documented Jira Cloud REST API v3 endpoints (not the third-party `jira` package — its exact method signatures couldn't be verified from available docs during planning, so this plan uses only endpoints confirmed against Atlassian's own API reference and community sources), `ruamel.yaml` (round-trip mode, to preserve `backlog.yml`'s comments — a plain YAML dump would destroy job_search's existing header documentation), `python-dotenv`, `unittest` + `coverage`.

**Spec:** [docs/superpowers/specs/2026-08-27-jira-integration-design.md](../specs/2026-08-27-jira-integration-design.md)

## Global Constraints

- Python 3.11 only (root `CLAUDE.md`)
- `black` (line length 88), `isort` (profile black), `ruff` — `python-style.md`
- Google-style docstrings on every public function/class, `Args`/`Returns`/`Raises` as relevant — `python-style.md`
- Type hints on all public signatures; `from __future__ import annotations` — `python-style.md`
- `unittest` + `coverage`; no mocking external APIs — integration tests gated on env vars instead — `python-testing.md`
- No bare `except:` — always a specific exception type — `python-style.md`
- Disjoint field ownership: this code NEVER writes status/assignee/comments/worklog/sprint to Jira, and NEVER reads them back — spec Non-goals
- `jira_key` fields in `backlog.yml` are only ever written by `sync_backlog`, never hand-edited — spec Architecture
- `ensure_project` always requests a Company-Managed template (`com.pyxis.greenhopper.jira:gh-scrum-template`) — Team-Managed projects cannot be created via the REST API — spec Context

---

## File Structure

**Package — `jira_sync_kit/` (new repo, already created)**

```
jira_sync_kit/
  __init__.py            __version__
  errors.py               JiraSyncError
  adf.py                  text_to_adf()
  backlog.py              load_backlog() / save_backlog() / iter_stories()
  client.py                JiraClient
  sync.py                   sync_backlog()
  __main__.py                CLI: ensure-project, create-bug, create-story, sync, init
  backlog.example.yml        packaged scaffold, shipped via `init`
setup.py
.env.example
tests/
  test_package.py
  test_adf.py
  test_backlog.py
  test_client.py           integration, gated on JIRA_* env vars
  test_sync.py               integration, gated
  test_main.py                 unit (parser only, no network)
```

**Template — `claude_project_template/`**

```
.claude/rules/jira-conventions.md      (new)
.claude/skills/jira-log/skill.md       (new)
CLAUDE.md                              (edit: one workflow line)
README.md                              (edit: document new files)
.env.example                           (edit: add JIRA_* placeholders)
```

**job_search adoption**

```
job_search/plan/backlog.yml            (moved from job_search/backlog.yml)
job_search/.env.example                (new, copied from template)
job_search/.env                        (new, gitignored, real credentials)
job_search/setup.py or requirements    (edit: add jira_sync_kit dependency)
```

---

## Phase A — `jira_sync_kit` package

### Task 1: Package scaffold

**Files:**
- Create: `jira_sync_kit/__init__.py`
- Create: `setup.py`
- Create: `tests/__init__.py`
- Create: `tests/test_package.py`

**Interfaces:**
- Produces: `jira_sync_kit.__version__: str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_package.py
import unittest


class TestPackageImport(unittest.TestCase):
    def test_package_exposes_version(self):
        import jira_sync_kit

        self.assertEqual(jira_sync_kit.__version__, "0.1.0")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_package -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jira_sync_kit'`

- [ ] **Step 3: Write minimal implementation**

```python
# jira_sync_kit/__init__.py
"""jira_sync_kit — reusable Jira Cloud client, backlog sync, and drafting CLI."""

__version__ = "0.1.0"
```

```python
# setup.py
from setuptools import find_packages, setup

setup(
    name="jira_sync_kit",
    version="0.1.0",
    packages=find_packages(exclude=("tests", "tests.*")),
    package_data={"jira_sync_kit": ["backlog.example.yml"]},
    include_package_data=True,
    python_requires=">=3.11",
    install_requires=[
        "requests>=2.31,<3",
        "ruamel.yaml>=0.18,<0.19",
        "python-dotenv>=1.0,<2",
    ],
    entry_points={"console_scripts": ["jira-sync-kit=jira_sync_kit.__main__:main"]},
)
```

- [ ] **Step 4: Install and run test to verify it passes**

Run: `python3.11 -m venv venv && source venv/bin/activate && pip install -e . && coverage run -m unittest discover && coverage report -m`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/__init__.py setup.py tests/__init__.py tests/test_package.py
git commit -m "chore: scaffold jira_sync_kit package"
```

---

### Task 2: Atlassian Document Format helper

**Files:**
- Create: `jira_sync_kit/adf.py`
- Test: `tests/test_adf.py`

**Interfaces:**
- Produces: `text_to_adf(text: str) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adf.py
import unittest

from jira_sync_kit.adf import text_to_adf


class TestTextToAdf(unittest.TestCase):
    def test_wraps_plain_text_in_a_paragraph(self):
        result = text_to_adf("Fix the login bug")

        self.assertEqual(
            result,
            {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Fix the login bug"}],
                    }
                ],
            },
        )

    def test_empty_string_produces_empty_content(self):
        result = text_to_adf("")
        self.assertEqual(result, {"type": "doc", "version": 1, "content": []})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_adf -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jira_sync_kit.adf'`

- [ ] **Step 3: Write minimal implementation**

```python
# jira_sync_kit/adf.py
"""Minimal Atlassian Document Format helpers for Jira API v3 rich-text fields."""
from __future__ import annotations


def text_to_adf(text: str) -> dict:
    """Wrap plain text in the minimal ADF structure the Jira v3 API requires.

    Args:
        text: Plain text to wrap. An empty string produces a doc with no
            paragraph content.

    Returns:
        An ADF document dict suitable for the `description` field.
    """
    content = []
    if text:
        content.append({"type": "paragraph", "content": [{"type": "text", "text": text}]})
    return {"type": "doc", "version": 1, "content": content}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_adf -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/adf.py tests/test_adf.py
git commit -m "feat: add plain-text to ADF converter"
```

---

### Task 3: backlog.yml load/save/walk (comment-preserving)

**Files:**
- Create: `jira_sync_kit/backlog.py`
- Test: `tests/test_backlog.py`

**Interfaces:**
- Produces: `load_backlog(path: str) -> dict`, `save_backlog(path: str, backlog: dict) -> None`, `iter_stories(backlog: dict) -> Iterator[tuple[dict, dict]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backlog.py
import os
import tempfile
import unittest

from jira_sync_kit.backlog import iter_stories, load_backlog, save_backlog

SAMPLE = """\
# header comment describing the contract with Jira
meta:
  project_key: TEST
  project_name: Test Project
epics:
  - key: E1
    summary: "Epic one"
    stories:
      - id: S1
        summary: "Story one"
        points: 3
"""


class TestBacklog(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".yml")
        os.close(fd)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SAMPLE)

    def tearDown(self):
        os.remove(self.path)

    def test_load_backlog_returns_parsed_meta(self):
        backlog = load_backlog(self.path)
        self.assertEqual(backlog["meta"]["project_key"], "TEST")

    def test_iter_stories_yields_epic_story_pairs(self):
        backlog = load_backlog(self.path)
        pairs = list(iter_stories(backlog))
        self.assertEqual(len(pairs), 1)
        epic, story = pairs[0]
        self.assertEqual(epic["key"], "E1")
        self.assertEqual(story["id"], "S1")

    def test_save_backlog_round_trips_a_new_field(self):
        backlog = load_backlog(self.path)
        _, story = next(iter_stories(backlog))
        story["jira_key"] = "TEST-42"
        save_backlog(self.path, backlog)

        reloaded = load_backlog(self.path)
        _, reloaded_story = next(iter_stories(reloaded))
        self.assertEqual(reloaded_story["jira_key"], "TEST-42")

    def test_save_backlog_preserves_header_comment(self):
        backlog = load_backlog(self.path)
        save_backlog(self.path, backlog)

        with open(self.path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# header comment describing the contract with Jira", content)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_backlog -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jira_sync_kit.backlog'`

- [ ] **Step 3: Write minimal implementation**

```python
# jira_sync_kit/backlog.py
"""Load, save, and walk a project's backlog.yml — the canonical delivery backlog.

Uses ruamel.yaml's round-trip mode so writing a `jira_key` back preserves
the file's comments and formatting. job_search's backlog.yml carries
several paragraphs of contract documentation as header comments; a plain
PyYAML dump would silently discard them on the first sync.
"""
from __future__ import annotations

from collections.abc import Iterator

from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True


def load_backlog(path: str) -> dict:
    """Read a backlog.yml file, preserving its formatting for a later save.

    Args:
        path: Filesystem path to the backlog YAML file.

    Returns:
        The parsed backlog (a ruamel CommentedMap, usable like a dict)
        with `meta` and `epics` keys.
    """
    with open(path, encoding="utf-8") as f:
        return _yaml.load(f)


def save_backlog(path: str, backlog: dict) -> None:
    """Write a backlog back to disk, preserving comments and key order.

    Args:
        path: Filesystem path to write to.
        backlog: The object returned by `load_backlog` — must be the same
            loaded object so ruamel retains its formatting metadata.
    """
    with open(path, "w", encoding="utf-8") as f:
        _yaml.dump(backlog, f)


def iter_stories(backlog: dict) -> Iterator[tuple[dict, dict]]:
    """Walk every (epic, story) pair in a backlog, in file order.

    Args:
        backlog: A backlog object as returned by `load_backlog`.

    Yields:
        (epic, story) tuples — both are the live objects from `backlog`,
        so mutating `story` (e.g. setting `jira_key`) mutates the backlog.
    """
    for epic in backlog.get("epics", []):
        for story in epic.get("stories", []):
            yield epic, story
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_backlog -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/backlog.py tests/test_backlog.py
git commit -m "feat: add comment-preserving backlog.yml load/save/walk"
```

---

### Task 4: JiraClient foundation (auth, request wrapper, account id)

**Files:**
- Create: `jira_sync_kit/errors.py`
- Create: `jira_sync_kit/client.py`
- Create: `tests/test_client.py`

**Interfaces:**
- Produces: `JiraSyncError(Exception)`; `JiraClient(site_url, email, api_token)`, `.get_account_id() -> str`

**Prerequisite:** a real Jira Free site and API token must already exist for these (and all later client/sync) tests to run — see Task 18 below. Until then, `test_client.py` and `test_sync.py` skip rather than fail.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client.py
import os
import unittest

from dotenv import load_dotenv

from jira_sync_kit.client import JiraClient

load_dotenv()

_HAS_CREDS = all(
    os.environ.get(v) for v in ("JIRA_SITE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
)


def make_client() -> JiraClient:
    return JiraClient(
        site_url=os.environ["JIRA_SITE_URL"],
        email=os.environ["JIRA_EMAIL"],
        api_token=os.environ["JIRA_API_TOKEN"],
    )


@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestJiraClientAccount(unittest.TestCase):
    def test_get_account_id_returns_non_empty_string(self):
        account_id = make_client().get_account_id()
        self.assertIsInstance(account_id, str)
        self.assertTrue(account_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_client -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jira_sync_kit.client'`

- [ ] **Step 3: Write minimal implementation**

```python
# jira_sync_kit/errors.py
"""Exceptions raised by jira_sync_kit."""


class JiraSyncError(Exception):
    """Raised when a Jira REST call fails or returns an unexpected response."""
```

```python
# jira_sync_kit/client.py
"""Jira Cloud REST API v3 client — the single place that talks to Jira.

Uses `requests` against documented, stable REST v3 endpoints rather than
a third-party Jira wrapper library, so every call maps to a specific,
verifiable Atlassian endpoint.
"""
from __future__ import annotations

import requests

from jira_sync_kit.errors import JiraSyncError


class JiraClient:
    """Thin wrapper around the Jira Cloud REST API v3.

    Attributes:
        site_url: Base URL of the Jira Cloud site, no trailing slash.
    """

    def __init__(self, site_url: str, email: str, api_token: str) -> None:
        """Initialise the client.

        Args:
            site_url: Base URL of the Jira Cloud site, e.g.
                "https://yoursite.atlassian.net".
            email: Atlassian account email used for Basic Auth.
            api_token: Jira API token used for Basic Auth.
        """
        self.site_url = site_url.rstrip("/")
        self._session = requests.Session()
        self._session.auth = (email, api_token)
        self._session.headers["Accept"] = "application/json"
        self._session.headers["Content-Type"] = "application/json"
        self._field_id_cache: dict[str, str] = {}

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Send a request and raise JiraSyncError on any non-2xx response.

        Args:
            method: HTTP method, e.g. "GET", "POST", "PUT".
            path: API path starting with "/rest/api/3/...".
            **kwargs: Passed through to `requests.Session.request`.

        Returns:
            The response object, guaranteed to have a 2xx status.

        Raises:
            JiraSyncError: If Jira returns a non-2xx status.
        """
        response = self._session.request(method, f"{self.site_url}{path}", **kwargs)
        if not response.ok:
            raise JiraSyncError(
                f"{method} {path} failed: {response.status_code} {response.text}"
            )
        return response

    def get_account_id(self) -> str:
        """Fetch the authenticated user's Jira accountId.

        Returns:
            The accountId string, used as the lead when creating a project.
        """
        return self._request("GET", "/rest/api/3/myself").json()["accountId"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_client -v`
Expected: PASS if `.env` has real credentials, SKIP (not FAIL) otherwise

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/errors.py jira_sync_kit/client.py tests/test_client.py
git commit -m "feat: add JiraClient with auth, request wrapper, get_account_id"
```

---

### Task 5: Project existence check and idempotent creation

**Files:**
- Modify: `jira_sync_kit/client.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Consumes: `self._request`, `self.get_account_id()` (Task 4)
- Produces: `.project_exists(key: str) -> bool`, `.ensure_project(key: str, name: str, template_key: str = "com.pyxis.greenhopper.jira:gh-scrum-template") -> str`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_client.py

@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestEnsureProject(unittest.TestCase):
    def test_ensure_project_is_idempotent(self):
        client = make_client()
        key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")

        first = client.ensure_project(key, "ZZ Test Project")
        second = client.ensure_project(key, "ZZ Test Project")

        self.assertEqual(first, key)
        self.assertEqual(second, key)
        self.assertTrue(client.project_exists(key))

    def test_project_exists_returns_false_for_unknown_key(self):
        client = make_client()
        self.assertFalse(client.project_exists("NOSUCHPROJECT99"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_client -v`
Expected: FAIL with `AttributeError: 'JiraClient' object has no attribute 'ensure_project'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to jira_sync_kit/client.py, inside JiraClient

    def project_exists(self, key: str) -> bool:
        """Check whether a project with the given key exists.

        Args:
            key: Jira project key, e.g. "JOB".

        Returns:
            True if the project exists, False if Jira returns 404.

        Raises:
            JiraSyncError: On any error response other than 404.
        """
        response = self._session.get(f"{self.site_url}/rest/api/3/project/{key}")
        if response.status_code == 404:
            return False
        if not response.ok:
            raise JiraSyncError(
                f"GET /rest/api/3/project/{key} failed: "
                f"{response.status_code} {response.text}"
            )
        return True

    def ensure_project(
        self,
        key: str,
        name: str,
        template_key: str = "com.pyxis.greenhopper.jira:gh-scrum-template",
    ) -> str:
        """Create the project if it doesn't exist yet; no-op otherwise.

        Args:
            key: Jira project key, e.g. "JOB".
            name: Human-readable project name.
            template_key: Jira project template key. Defaults to the
                classic Company-Managed Scrum template — Team-Managed
                projects cannot be created via the REST API.

        Returns:
            The project key (returned for chaining/logging).
        """
        if self.project_exists(key):
            return key
        self._request(
            "POST",
            "/rest/api/3/project",
            json={
                "key": key,
                "name": name,
                "projectTypeKey": "software",
                "projectTemplateKey": template_key,
                "leadAccountId": self.get_account_id(),
            },
        )
        return key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_client -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/client.py tests/test_client.py
git commit -m "feat: add idempotent ensure_project"
```

---

### Task 6: Custom field id resolution (Story Points, Epic Link)

**Files:**
- Modify: `jira_sync_kit/client.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Produces: `.resolve_custom_field_id(field_name: str) -> str | None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_client.py

@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestResolveCustomFieldId(unittest.TestCase):
    def test_resolves_story_points_field(self):
        client = make_client()
        field_id = client.resolve_custom_field_id("Story Points")
        self.assertTrue(field_id is None or field_id.startswith("customfield_"))

    def test_unknown_field_name_returns_none(self):
        client = make_client()
        self.assertIsNone(client.resolve_custom_field_id("Not A Real Field Name"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_client -v`
Expected: FAIL with `AttributeError: 'JiraClient' object has no attribute 'resolve_custom_field_id'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to jira_sync_kit/client.py, inside JiraClient

    def resolve_custom_field_id(self, field_name: str) -> str | None:
        """Look up a custom field's id by its display name, cached per client.

        Field ids like "customfield_10016" vary per Jira site, so callers
        must resolve by name (e.g. "Story Points", "Epic Link") instead of
        hardcoding an id.

        Args:
            field_name: Exact display name of the field.

        Returns:
            The field id, or None if no field with that name exists.
        """
        if field_name not in self._field_id_cache:
            fields = self._request("GET", "/rest/api/3/field").json()
            match = next((f for f in fields if f["name"] == field_name), None)
            self._field_id_cache[field_name] = match["id"] if match else ""
        return self._field_id_cache[field_name] or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_client -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/client.py tests/test_client.py
git commit -m "feat: add custom field id resolution by display name"
```

---

### Task 7: Issue creation (Epic/Story/Sub-task/Bug)

**Files:**
- Modify: `jira_sync_kit/client.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Consumes: `text_to_adf` (Task 2), `.resolve_custom_field_id` (Task 6)
- Produces: `.create_issue(project_key, issue_type, summary, description="", labels=None, parent_key=None, epic_key=None, story_points=None) -> str`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_client.py

@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestCreateIssue(unittest.TestCase):
    def setUp(self):
        self.client = make_client()
        self.project_key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")
        self.client.ensure_project(self.project_key, "ZZ Test Project")

    def test_create_bug_returns_a_key_in_the_right_project(self):
        key = self.client.create_issue(
            self.project_key, "Bug", "Test bug from create_issue", "Steps to reproduce"
        )
        self.assertTrue(key.startswith(f"{self.project_key}-"))

    def test_create_subtask_under_a_parent(self):
        parent_key = self.client.create_issue(self.project_key, "Story", "Parent story for subtask test")
        child_key = self.client.create_issue(
            self.project_key, "Sub-task", "Subtask of parent", parent_key=parent_key
        )
        self.assertTrue(child_key.startswith(f"{self.project_key}-"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_client -v`
Expected: FAIL with `AttributeError: 'JiraClient' object has no attribute 'create_issue'`

- [ ] **Step 3: Write minimal implementation**

```python
# add near the top of jira_sync_kit/client.py
from jira_sync_kit.adf import text_to_adf
```

```python
# add to jira_sync_kit/client.py, inside JiraClient

    def create_issue(
        self,
        project_key: str,
        issue_type: str,
        summary: str,
        description: str = "",
        labels: list[str] | None = None,
        parent_key: str | None = None,
        epic_key: str | None = None,
        story_points: int | None = None,
    ) -> str:
        """Create an issue and return its key.

        Args:
            project_key: Jira project key to create the issue in.
            issue_type: Issue type name, e.g. "Epic", "Story", "Sub-task", "Bug".
            summary: Issue summary/title.
            description: Plain-text description, converted to ADF.
            labels: Labels to attach.
            parent_key: For a Sub-task, the parent Story's key (native
                `parent` field).
            epic_key: For a Story, the Epic's key (resolved "Epic Link"
                custom field — Sub-tasks don't use this).
            story_points: Story points value; skipped if this site has no
                "Story Points" field.

        Returns:
            The created issue's key, e.g. "JOB-42".
        """
        fields: dict = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            "summary": summary,
            "description": text_to_adf(description),
        }
        if labels:
            fields["labels"] = labels
        if parent_key:
            fields["parent"] = {"key": parent_key}
        if epic_key:
            field_id = self.resolve_custom_field_id("Epic Link")
            if field_id:
                fields[field_id] = epic_key
        if story_points is not None:
            field_id = self.resolve_custom_field_id("Story Points")
            if field_id:
                fields[field_id] = story_points
        response = self._request("POST", "/rest/api/3/issue", json={"fields": fields})
        return response.json()["key"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_client -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/client.py tests/test_client.py
git commit -m "feat: add create_issue for epics/stories/subtasks/bugs"
```

---

### Task 8: Issue update (content fields only)

**Files:**
- Modify: `jira_sync_kit/client.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Produces: `.update_issue(issue_key, summary=None, description=None, labels=None, story_points=None) -> None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_client.py

@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestUpdateIssue(unittest.TestCase):
    def test_update_issue_changes_summary(self):
        client = make_client()
        project_key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")
        client.ensure_project(project_key, "ZZ Test Project")
        key = client.create_issue(project_key, "Bug", "Original summary")

        client.update_issue(key, summary="Updated summary")

        response = client._request("GET", f"/rest/api/3/issue/{key}")
        self.assertEqual(response.json()["fields"]["summary"], "Updated summary")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_client -v`
Expected: FAIL with `AttributeError: 'JiraClient' object has no attribute 'update_issue'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to jira_sync_kit/client.py, inside JiraClient

    def update_issue(
        self,
        issue_key: str,
        summary: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        story_points: int | None = None,
    ) -> None:
        """Update an existing issue's content fields.

        Never touches status, assignee, comments, worklog, or sprint —
        those are Jira-owned per the disjoint field-ownership contract.

        Args:
            issue_key: The issue to update, e.g. "JOB-12".
            summary: New summary, if changing.
            description: New plain-text description, if changing.
            labels: New label list, if changing.
            story_points: New story points value, if changing.
        """
        fields: dict = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = text_to_adf(description)
        if labels is not None:
            fields["labels"] = labels
        if story_points is not None:
            field_id = self.resolve_custom_field_id("Story Points")
            if field_id:
                fields[field_id] = story_points
        if fields:
            self._request("PUT", f"/rest/api/3/issue/{issue_key}", json={"fields": fields})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_client -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/client.py tests/test_client.py
git commit -m "feat: add update_issue for content-only fields"
```

---

### Task 9: "Blocks" issue linking

**Files:**
- Modify: `jira_sync_kit/client.py`
- Modify: `tests/test_client.py`

**Interfaces:**
- Produces: `.link_blocks(blocking_key: str, blocked_key: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_client.py

@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestLinkBlocks(unittest.TestCase):
    def test_link_blocks_does_not_raise(self):
        client = make_client()
        project_key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")
        client.ensure_project(project_key, "ZZ Test Project")
        a = client.create_issue(project_key, "Story", "Blocking story")
        b = client.create_issue(project_key, "Story", "Blocked story")

        client.link_blocks(a, b)  # should not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_client -v`
Expected: FAIL with `AttributeError: 'JiraClient' object has no attribute 'link_blocks'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to jira_sync_kit/client.py, inside JiraClient

    def link_blocks(self, blocking_key: str, blocked_key: str) -> None:
        """Create a "blocks" link: `blocking_key` blocks `blocked_key`.

        Args:
            blocking_key: The issue that blocks.
            blocked_key: The issue that is blocked.
        """
        self._request(
            "POST",
            "/rest/api/3/issueLink",
            json={
                "type": {"name": "Blocks"},
                "inwardIssue": {"key": blocked_key},
                "outwardIssue": {"key": blocking_key},
            },
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_client -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/client.py tests/test_client.py
git commit -m "feat: add link_blocks for backlog blocks dependencies"
```

---

### Task 10: `sync_backlog` — full idempotent walk

**Files:**
- Create: `jira_sync_kit/sync.py`
- Create: `tests/test_sync.py`

**Interfaces:**
- Consumes: `load_backlog`, `save_backlog`, `iter_stories` (Task 3); `JiraClient.ensure_project`, `.create_issue`, `.update_issue`, `.link_blocks` (Tasks 5, 7–9)
- Produces: `sync_backlog(client: JiraClient, backlog_path: str) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sync.py
import os
import tempfile
import unittest

from dotenv import load_dotenv

from jira_sync_kit.backlog import load_backlog
from jira_sync_kit.client import JiraClient
from jira_sync_kit.sync import sync_backlog

load_dotenv()

_HAS_CREDS = all(
    os.environ.get(v) for v in ("JIRA_SITE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
)

_PROJECT_KEY = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")

FIXTURE = f"""\
meta:
  project_key: {_PROJECT_KEY}
  project_name: ZZ Test Project
epics:
  - key: E1
    summary: "Fixture epic"
    stories:
      - id: S1
        summary: "Fixture story"
        points: 2
        subtasks:
          - "Fixture subtask one"
"""


@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestSyncBacklog(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".yml")
        os.close(fd)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(FIXTURE)
        self.client = JiraClient(
            site_url=os.environ["JIRA_SITE_URL"],
            email=os.environ["JIRA_EMAIL"],
            api_token=os.environ["JIRA_API_TOKEN"],
        )

    def tearDown(self):
        os.remove(self.path)

    def test_second_run_creates_nothing_new(self):
        first = sync_backlog(self.client, self.path)
        self.assertEqual(len(first["created"]), 2)  # epic + story

        second = sync_backlog(self.client, self.path)
        self.assertEqual(second["created"], [])
        self.assertEqual(len(second["updated"]), 2)

    def test_jira_key_is_written_back_to_the_file(self):
        sync_backlog(self.client, self.path)
        backlog = load_backlog(self.path)
        self.assertTrue(backlog["epics"][0]["jira_key"].startswith(f"{_PROJECT_KEY}-"))
        self.assertTrue(
            backlog["epics"][0]["stories"][0]["jira_key"].startswith(f"{_PROJECT_KEY}-")
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_sync -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jira_sync_kit.sync'`

- [ ] **Step 3: Write minimal implementation**

```python
# jira_sync_kit/sync.py
"""One-way, idempotent sync of a project's backlog.yml into Jira."""
from __future__ import annotations

from jira_sync_kit.backlog import iter_stories, load_backlog, save_backlog
from jira_sync_kit.client import JiraClient


def sync_backlog(client: JiraClient, backlog_path: str) -> dict:
    """Walk a backlog.yml and create-or-update every epic/story in Jira.

    A story with a `jira_key` is updated in place, never recreated.
    Subtasks are created only the first time their parent story is
    created — backlog.yml tracks no per-subtask jira_key, matching the
    original Step 0 design.

    Args:
        client: A configured JiraClient.
        backlog_path: Path to the project's backlog.yml.

    Returns:
        {"created": [issue keys], "updated": [issue keys]}
    """
    backlog = load_backlog(backlog_path)
    meta = backlog["meta"]
    project_key = meta["project_key"]
    client.ensure_project(project_key, meta["project_name"])

    created: list[str] = []
    updated: list[str] = []
    epic_keys: dict[str, str] = {}

    for epic in backlog.get("epics", []):
        if epic.get("jira_key"):
            client.update_issue(
                epic["jira_key"],
                summary=epic["summary"],
                description=epic.get("description", ""),
            )
            updated.append(epic["jira_key"])
        else:
            jira_key = client.create_issue(
                project_key,
                "Epic",
                epic["summary"],
                epic.get("description", ""),
                labels=epic.get("labels"),
            )
            epic["jira_key"] = jira_key
            created.append(jira_key)
        epic_keys[epic["key"]] = epic["jira_key"]

    for epic, story in iter_stories(backlog):
        story_is_new = not story.get("jira_key")
        if story_is_new:
            jira_key = client.create_issue(
                project_key,
                "Story",
                story["summary"],
                story.get("description", ""),
                labels=story.get("labels"),
                epic_key=epic_keys.get(epic["key"]),
                story_points=story.get("points"),
            )
            story["jira_key"] = jira_key
            created.append(jira_key)
        else:
            client.update_issue(
                story["jira_key"],
                summary=story["summary"],
                description=story.get("description", ""),
                story_points=story.get("points"),
            )
            updated.append(story["jira_key"])

        if story_is_new:
            for subtask_summary in story.get("subtasks", []):
                client.create_issue(
                    project_key, "Sub-task", subtask_summary, parent_key=story["jira_key"]
                )

    for _, story in iter_stories(backlog):
        for blocked_id in story.get("blocks", []):
            blocked = next((s for _, s in iter_stories(backlog) if s["id"] == blocked_id), None)
            if blocked and blocked.get("jira_key") and story.get("jira_key"):
                client.link_blocks(story["jira_key"], blocked["jira_key"])

    save_backlog(backlog_path, backlog)
    return {"created": created, "updated": updated}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_sync -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/sync.py tests/test_sync.py
git commit -m "feat: add idempotent sync_backlog walking epics/stories/subtasks/blocks"
```

---

### Task 11: CLI

**Files:**
- Create: `jira_sync_kit/__main__.py`
- Create: `tests/test_main.py`

**Interfaces:**
- Consumes: `JiraClient` (Task 4), `sync_backlog` (Task 10)
- Produces: `build_parser() -> argparse.ArgumentParser`, `main(argv: list[str] | None = None) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main.py
import unittest

from jira_sync_kit.__main__ import build_parser


class TestBuildParser(unittest.TestCase):
    def test_create_bug_parses_required_args(self):
        args = build_parser().parse_args(
            ["create-bug", "--project-key", "JOB", "--summary", "x"]
        )
        self.assertEqual(args.command, "create-bug")
        self.assertEqual(args.project_key, "JOB")
        self.assertEqual(args.labels, [])

    def test_sync_defaults_backlog_path(self):
        args = build_parser().parse_args(["sync"])
        self.assertEqual(args.backlog, "plan/backlog.yml")

    def test_init_defaults_out_path(self):
        args = build_parser().parse_args(["init"])
        self.assertEqual(args.out, "plan/backlog.yml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_main -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jira_sync_kit.__main__'`

- [ ] **Step 3: Write minimal implementation**

```python
# jira_sync_kit/__main__.py
"""CLI for jira_sync_kit: ensure-project, create-bug, create-story, sync, init."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from importlib import resources

from dotenv import load_dotenv

from jira_sync_kit.client import JiraClient
from jira_sync_kit.sync import sync_backlog


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser. No side effects — safe to unit test.

    Returns:
        A configured ArgumentParser with all subcommands registered.
    """
    parser = argparse.ArgumentParser(prog="jira_sync_kit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure = subparsers.add_parser("ensure-project", help="Create the Jira project if missing")
    ensure.add_argument("--key", required=True)
    ensure.add_argument("--name", required=True)

    bug = subparsers.add_parser("create-bug", help="Create a Bug directly in Jira")
    bug.add_argument("--project-key", required=True)
    bug.add_argument("--summary", required=True)
    bug.add_argument("--description", default="")
    bug.add_argument("--labels", nargs="*", default=[])

    story = subparsers.add_parser("create-story", help="Create a Story under an epic")
    story.add_argument("--project-key", required=True)
    story.add_argument("--epic-key", required=True)
    story.add_argument("--summary", required=True)
    story.add_argument("--description", default="")
    story.add_argument("--points", type=int, default=None)
    story.add_argument("--labels", nargs="*", default=[])

    sync_parser = subparsers.add_parser("sync", help="Sync backlog.yml into Jira")
    sync_parser.add_argument("--backlog", default="plan/backlog.yml")

    init_parser = subparsers.add_parser("init", help="Scaffold a new backlog.yml")
    init_parser.add_argument("--out", default="plan/backlog.yml")

    return parser


def _client_from_env() -> JiraClient:
    """Build a JiraClient from JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN.

    Returns:
        A configured JiraClient.

    Raises:
        SystemExit: If any required env var is missing.
    """
    load_dotenv()
    try:
        return JiraClient(
            site_url=os.environ["JIRA_SITE_URL"],
            email=os.environ["JIRA_EMAIL"],
            api_token=os.environ["JIRA_API_TOKEN"],
        )
    except KeyError as exc:
        sys.exit(f"Missing required env var: {exc}")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    Args:
        argv: Argument list, defaults to sys.argv[1:] when None.
    """
    args = build_parser().parse_args(argv)

    if args.command == "init":
        source = resources.files("jira_sync_kit").joinpath("backlog.example.yml")
        shutil.copyfile(str(source), args.out)
        print(f"Wrote {args.out}")
        return

    client = _client_from_env()

    if args.command == "ensure-project":
        client.ensure_project(args.key, args.name)
        print(f"Project {args.key} ready")
    elif args.command == "create-bug":
        client.ensure_project(args.project_key, args.project_key)
        print(client.create_issue(args.project_key, "Bug", args.summary, args.description, args.labels))
    elif args.command == "create-story":
        client.ensure_project(args.project_key, args.project_key)
        print(
            client.create_issue(
                args.project_key,
                "Story",
                args.summary,
                args.description,
                args.labels,
                epic_key=args.epic_key,
                story_points=args.points,
            )
        )
    elif args.command == "sync":
        result = sync_backlog(client, args.backlog)
        print(f"Created: {len(result['created'])}, Updated: {len(result['updated'])}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_main -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/__main__.py tests/test_main.py
git commit -m "feat: add CLI (ensure-project, create-bug, create-story, sync, init)"
```

---

### Task 12: Packaged `backlog.example.yml` and `init` round-trip

**Files:**
- Create: `jira_sync_kit/backlog.example.yml`
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: `main()` (Task 11)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_main.py
import os
import tempfile


class TestInitCommand(unittest.TestCase):
    def test_init_writes_a_backlog_with_meta_and_epics(self):
        from jira_sync_kit.__main__ import main
        from jira_sync_kit.backlog import load_backlog

        out_dir = tempfile.mkdtemp()
        out_path = os.path.join(out_dir, "backlog.yml")

        main(["init", "--out", out_path])

        backlog = load_backlog(out_path)
        self.assertIn("project_key", backlog["meta"])
        self.assertTrue(len(backlog["epics"]) >= 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_main -v`
Expected: FAIL — `FileNotFoundError` (package data file missing)

- [ ] **Step 3: Write minimal implementation**

```yaml
# jira_sync_kit/backlog.example.yml
# Copy this to plan/backlog.yml in a new project and fill it in.
# See docs/superpowers/specs/2026-08-27-jira-integration-design.md for the
# full contract (disjoint field ownership, idempotency via jira_key).

meta:
  project_key: CHANGEME
  project_name: Change Me Project
  project_template: com.pyxis.greenhopper.jira:gh-scrum-template
  issue_types:
    epic: Epic
    story: Story
    subtask: Sub-task
    bug: Bug
  default_labels: []
  label_vocabulary: []
  point_scale: [1, 2, 3, 5, 8]

epics:
  - key: EXAMPLE
    summary: "Example epic — replace or delete"
    description: >
      Delete this epic once you've added your real ones.
    labels: []
    stories:
      - id: EXAMPLE-01
        summary: "Example story"
        description: >
          Delete this story once you've added your real ones.
        acceptance: >
          What "done" means for this story.
        labels: []
        points: 1
        subtasks:
          - "Example subtask"
```

Also update `setup.py`'s `package_data` (already set in Task 1) — no change needed since `package_data={"jira_sync_kit": ["backlog.example.yml"]}` already covers this file; re-run `pip install -e .` so it's picked up.

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install -e . && python -m unittest tests.test_main -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jira_sync_kit/backlog.example.yml tests/test_main.py
git commit -m "feat: add packaged backlog.example.yml, tested via init"
```

---

### Task 13: README, `.env.example`, and initial release tag

**Files:**
- Modify: `README.md`
- Create: `.env.example`

- [ ] **Step 1: Write `.env.example`**

```bash
# .env.example
JIRA_SITE_URL=https://yoursite.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=
# Optional — overrides the default "ZZTEST" project used by this
# package's own integration tests, so they never touch a real project.
JIRA_TEST_PROJECT_KEY=ZZTEST
```

- [ ] **Step 2: Rewrite `README.md`** to document the package's actual public surface (client, sync, CLI subcommands, `.env` vars) in place of the "Status: scaffolding only" placeholder from repo creation.

- [ ] **Step 3: Run the full suite one more time**

Run: `coverage run -m unittest discover && coverage report -m`
Expected: PASS, ≥80% line coverage per `python-testing.md`

- [ ] **Step 4: Commit, tag, and push**

```bash
git add README.md .env.example
git commit -m "docs: document public API and required env vars"
git tag 0.1.0 -m "Initial release: client, sync, CLI"
git push origin main --tags
```

---

## Phase B — Template and job_search adoption

### Task 14: `claude_project_template/.claude/rules/jira-conventions.md`

**Files:**
- Create: `claude_project_template/.claude/rules/jira-conventions.md`

- [ ] **Step 1: Write the file**

```markdown
# Jira Conventions

Applies to any project with a `plan/backlog.yml` and the `jira_sync_kit` package installed.

## Issue-type mapping

| Branch-type classification | Jira issue type | Created by |
|---|---|---|
| `feat` | Story (under the matched or a new Epic) | `jira-log` skill, on confirmation |
| `fix` | Bug | `jira-log` skill, on confirmation |
| `chore` / `refactor` / `test` | Sub-task, under the currently active story | `jira-log` skill, on confirmation |
| `docs` | No ticket | — |

## Field ownership (disjoint — never violate this)

| `jira_sync_kit` writes | Jira alone owns |
|---|---|
| summary, description | status |
| acceptance criteria, labels | assignee |
| story points | comments, worklog |
| issue links, subtasks | sprint assignment |

Never add code that reads status/assignee/comments/worklog/sprint back
from Jira. This disjoint ownership is what makes the sync safe to re-run
without conflict resolution.

## Idempotency

`jira_key` in `backlog.yml` is written only by `sync_backlog` (via
`python -m jira_sync_kit sync`). Never hand-edit a `jira_key` value — a
story with one is updated in place on the next sync, never recreated.

## Confirm-before-write

Every Jira write triggered by a session (a drafted Bug or a drafted
Story) requires the user's explicit confirmation first. Nothing is
created silently.

## Burndown charts

Native Jira Scrum-board feature — no code involved. Start a sprint on the
project's board once it has issues with story points; the burndown chart
appears under the board's Reports tab automatically.
```

- [ ] **Step 2: Commit**

```bash
git add claude_project_template/.claude/rules/jira-conventions.md
git commit -m "feat(claude-skills): add jira-conventions rule to project template"
```

---

### Task 15: `claude_project_template/.claude/skills/jira-log/skill.md`

**Files:**
- Create: `claude_project_template/.claude/skills/jira-log/skill.md`

- [ ] **Step 1: Write the file**

```markdown
---
name: jira-log
description: Draft and, on confirmation, create a Jira ticket for a confirmed fix or feature. Use when CLAUDE.md's branch-type question has just classified work as `fix` (draft a Bug) or `feat` (draft a Story/Task), and this project has a `plan/backlog.yml`.
---

# Jira Log

Records confirmed ad-hoc bugs and features as Jira tickets. Never runs
silently — every write requires explicit user confirmation. See
`.claude/rules/jira-conventions.md` for the full field-ownership contract
this skill must not violate.

## Preconditions

If `plan/backlog.yml` does not exist in this project, do nothing — this
project hasn't adopted Jira tracking. Don't mention it unless asked.

## On a confirmed `fix`

1. Draft a Bug: a one-line summary, a short description of what was
   broken and the fix, and labels drawn from `plan/backlog.yml`'s
   `meta.label_vocabulary` if any apply.
2. Show the draft to the user and ask for confirmation before writing
   anything.
3. On confirmation, run:
   ```bash
   python -m jira_sync_kit create-bug \
     --project-key <meta.project_key from backlog.yml> \
     --summary "<summary>" \
     --description "<description>" \
     --labels <label1> <label2>
   ```
4. Report the created issue key back to the user.

## On a confirmed `feat`

1. Draft a Story: summary, description, acceptance criteria, points
   estimate, labels. Find the epic it belongs under by scanning
   `plan/backlog.yml`'s `epics[].summary` for the closest match; if none
   fits, propose a new epic instead of forcing a mismatch.
2. Show the draft (including which epic, existing or new) to the user
   and ask for confirmation before writing anything.
3. On confirmation:
   - Append the story (and new epic, if proposed) to `plan/backlog.yml`,
     following the existing file's structure exactly — do not reformat
     unrelated parts of the file.
   - Run `python -m jira_sync_kit sync` to push it to Jira.
4. Report the created issue key(s) back to the user.

## On `chore` / `refactor` / `test`

Same as `feat`, but draft a Sub-task under the story currently being
worked on (if one is active in this session) rather than a new Story. If
no story is active, skip silently — don't force a ticket where there's no
natural parent.

## Never

- Create anything without showing the draft and getting confirmation first.
- Auto-file from application logs — this skill only fires from an
  explicit session-time classification, never from log scanning.
- Read or write Jira's status, assignee, comments, worklog, or sprint fields.
```

- [ ] **Step 2: Commit**

```bash
git add claude_project_template/.claude/skills/jira-log/skill.md
git commit -m "feat(claude-skills): add jira-log drafting skill to project template"
```

---

### Task 16: Template `CLAUDE.md`, `README.md`, `.env.example` updates

**Files:**
- Modify: `claude_project_template/CLAUDE.md`
- Modify: `claude_project_template/README.md`
- Create: `claude_project_template/.env.example`

- [ ] **Step 1: Add one line to the Development Workflow section of `claude_project_template/CLAUDE.md`**, immediately after the existing branch-type question:

```markdown
2. **If `plan/backlog.yml` exists**, use the `jira-log` skill to record the confirmed fix/feature as a Jira ticket.
```

(Renumber the existing steps 2–4 to 3–5.)

- [ ] **Step 2: Add a row to `claude_project_template/README.md`'s "Skills" list**

```markdown
- **`jira-log`** — drafts a Bug (on `fix`) or Story/Task (on `feat`), and on your confirmation creates it via the installed `jira_sync_kit` package — see `.claude/rules/jira-conventions.md`
```

And add a new subsection after "Using this as a template":

```markdown
## Optional: Jira tracking

Projects that want epic/story/subtask/bug tracking in Jira:

1. `pip install git+https://github.com/FredGH/jira_sync_kit.git@<tag>`
2. `python -m jira_sync_kit init` to scaffold `plan/backlog.yml`, then fill it in
3. Copy `JIRA_SITE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` from `.env.example` into `.env` with real values
4. `python -m jira_sync_kit sync` — creates the Jira project (if missing) and the full backlog; safe to re-run
```

- [ ] **Step 3: Create `claude_project_template/.env.example`**

```bash
# .env.example
JIRA_SITE_URL=https://yoursite.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=
```

- [ ] **Step 4: Commit**

```bash
git add claude_project_template/CLAUDE.md claude_project_template/README.md claude_project_template/.env.example
git commit -m "feat(claude-skills): document Jira tracking as an opt-in template feature"
```

---

### Task 17: job_search adoption — relocate backlog, install package

**Files:**
- Modify: `job_search/backlog.yml` → `job_search/plan/backlog.yml` (moved)
- Modify: job_search's dependency list (`setup.py` or equivalent)
- Create: `job_search/.env.example`

- [ ] **Step 1: Move the backlog into `plan/`**

```bash
mkdir -p job_search/plan
git mv job_search/backlog.yml job_search/plan/backlog.yml
```

- [ ] **Step 2: Add `jira_sync_kit` to job_search's dependencies**, pinned to the tag pushed in Task 13:

```bash
pip3.11 install git+https://github.com/FredGH/jira_sync_kit.git@0.1.0
```

Add the same line to whatever job_search uses to declare dependencies (`setup.py`'s `install_requires` or `requirements.txt` — check which exists in `job_search/` before editing).

- [ ] **Step 3: Create `job_search/.env.example`** (copy of the template's, Task 16)

```bash
cp claude_project_template/.env.example job_search/.env.example
```

- [ ] **Step 4: Commit**

```bash
git add job_search/plan/backlog.yml job_search/.env.example
git status  # confirm backlog.yml shows as renamed, not deleted+added
git commit -m "chore(job_search): relocate backlog.yml into plan/, add jira_sync_kit dependency"
```

---

### Task 18 (MANUAL — user action): Jira Free site and API token

Not automatable — a new Atlassian site requires a human signup.

- [ ] Sign up for a Jira Free site at atlassian.com (skip if one already exists for this account)
- [ ] Generate an API token: Atlassian account settings → Security → API tokens
- [ ] Copy `job_search/.env.example` to `job_search/.env` and fill in `JIRA_SITE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`
- [ ] Confirm `job_search/.env` is covered by `.gitignore` (it should already match the existing `*.env`/`.env` pattern — verify, don't assume)

Once this is done, Tasks 4–13's integration tests (currently skipped) will run for real against this account, and the free-tier `ZZTEST` project will get created by them.

---

### Task 19 (verification, requires 18 done): First real sync of job_search's backlog

- [ ] Run `python -m jira_sync_kit sync --backlog plan/backlog.yml` from `job_search/` once — confirm it creates the `JOB` project and the full epic/story/subtask hierarchy, with zero errors
- [ ] Run it a second time — confirm `git diff job_search/plan/backlog.yml` shows no changes (all `jira_key` fields already present, nothing recreated) — this is Step 0's original "Done when" criterion from `PLAN.md`
- [ ] Spot-check 2–3 issues in the Jira UI: correct project, correct epic/story/subtask nesting, labels present

---

### Task 20 (MANUAL — Jira UI, requires 19 done): Enable the burndown chart

No code — Jira draws this natively.

- [ ] In the `JOB` project's Scrum board, move a batch of stories into a new sprint
- [ ] Start the sprint
- [ ] Confirm the Burndown Chart report (board → Reports → Burndown Chart) now renders

---

## Self-Review

**Spec coverage:** Package (Architecture table) → Tasks 1–13. Template glue (jira-log skill, jira-conventions rule, CLAUDE.md line, README, .env.example) → Tasks 14–16. job_search-specific items (backlog.yml relocation, `.env`, install) → Task 17. Manual/human items (Jira site signup, first sync verification, burndown sprint start) → Tasks 18–20, explicitly marked non-automatable. Error handling section → `_request`'s `JiraSyncError` (Task 4) used throughout; no swallowed exceptions anywhere in the plan. Testing section → integration tests gated on `JIRA_*` via `unittest.skipUnless` (Tasks 4–10), `JIRA_TEST_PROJECT_KEY` isolates test runs from the real `JOB` project.

**Placeholder scan:** none found — every step has real code or, for 14/15/18–20, real file content or concrete manual instructions.

**Type/signature consistency:** `JiraClient.create_issue`'s `epic_key`/`parent_key`/`story_points` parameters (Task 7) match how `sync.py` calls them (Task 10) and how the CLI calls them (Task 11). `resolve_custom_field_id` (Task 6) is used consistently by both `create_issue` and `update_issue`. `sync_backlog`'s return shape `{"created": [...], "updated": [...]}` (Task 10) matches how `__main__.py` reads `result['created']`/`result['updated']` (Task 11).
