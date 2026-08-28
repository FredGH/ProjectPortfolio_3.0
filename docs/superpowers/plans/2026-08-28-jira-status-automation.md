# Jira Status Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `jira_sync_kit` a narrow, one-way, forward-only `status` write path — triggered by branch creation and PR merge, not drafted intent — and wire it into `commit-push`/`commit-push-pr`, per the design spec.

**Architecture:** `jira_sync_kit` (separate repo, `~/Documents/GitHub/jira_sync_kit`) gains six new `JiraClient` methods and two new CLI subcommands (`start-story`, `complete-story`). `claude_project_template`'s `commit-push`/`commit-push-pr` skills call those subcommands silently at the exact moment a branch is created / a PR merges. `job_search` (the only project currently using Jira tracking) adopts the new version; the same skill-file edits are propagated to the other sibling projects for consistency, matching the precedent set by the prior Jira-integration propagation commit.

**Tech Stack:** Python 3.11, `requests` against Jira Cloud REST API v3 (`/rest/api/3/...`) and Agile API v1.0 (`/rest/agile/1.0/...`), `unittest` + `coverage`, real API integration tests against the disposable `ZZTEST` project (no mocking).

**Spec:** [docs/superpowers/specs/2026-08-28-jira-status-automation-design.md](../specs/2026-08-28-jira-status-automation-design.md)

## Spec corrections & decisions (from prerequisite API verification)

The spec required verifying the Agile API's exact shapes before writing this plan (its "Prerequisite" section). That verification was done against the real `ZZTEST` project/board on the live Jira site and surfaced a few things the spec left open or got wrong:

1. **Backward-transition prevention needs an explicit check — the spec's assumed mechanism doesn't hold.** The spec's Error Handling section assumed `get_transitions()` only ever returns forward-reachable states, so "no matching transition" alone would signal "already there or past." Verified false: on this site's workflow, every status (`To Do`, `In Progress`, `Done`) is listed as an available transition from every other status — moving an issue to `In Progress` and then re-querying transitions still offers `To Do`. A literal implementation of the spec would therefore let backward transitions through, violating the spec's own Non-goal ("must never move it backward"). Fix: `transition_issue` compares `statusCategory.key` rank (`new`=0 < `indeterminate`=1 < `done`=2) between the issue's *current* status and the matching transition's target, and treats `target_rank <= current_rank` as the no-op case — not "transition absent from the list."
2. **No `JIRA_BOARD_ID` env var needed.** `GET /rest/agile/1.0/issue/{key}` returns a top-level `sprint` field directly (id, state, name, boardId) on any sprint-assigned issue — `complete-story` resolves the sprint straight from the just-transitioned issue, no board lookup required. Added `get_issue_sprint_id(issue_key) -> int | None` to `JiraClient` for this — it's not in the spec's method table, but is required to fulfil the spec's own documented CLI behavior ("`complete-story` ... using the board/sprint resolved from the issue's own sprint field").
3. **Sprint completion is `PUT /rest/agile/1.0/sprint/{id}` with the full body** (`name`, `state: "closed"`, `startDate`, `endDate` — all required, echoed back from a prior `GET`), not a partial `{"state": "closed"}` patch or a dedicated action endpoint. Same shape starts a sprint (`state: "active"`).
4. **`--dry-run` flag: added**, per the spec's own open question — cheap to add, and valuable now that these are silent/automatic calls with no confirmation step. Both `start-story` and `complete-story` get it.
5. **No branch-naming migration needed.** Confirmed: `resolve_issue_key_from_branch` returning `None` for pre-existing `<type>/<slug>` branches is already a complete answer — nothing to retrofit. Documented explicitly in `jira-conventions.md` (Task 13) so it isn't re-litigated later.
6. **`complete_sprint_if_done` treats an empty sprint as a no-op**, not vacuously "done" (`all([])` is `True` in Python, which would otherwise close a sprint with zero issues in it). Not covered by the spec text; chosen to match its "no partial action" philosophy conservatively.

All six of Jira's verified request/response shapes are reproduced inline in the tasks below.

## Global Constraints

- Python 3.11 only (root `CLAUDE.md`)
- `black` (line length 88), `isort` (profile black), `ruff` — `python-style.md`
- Google-style docstrings on every public function/class, `Args`/`Returns`/`Raises` as relevant — `python-style.md`
- Type hints on all public signatures; `from __future__ import annotations` — `python-style.md`
- `unittest` + `coverage`; no mocking external APIs — integration tests gated on `JIRA_*` env vars via `unittest.skipUnless` instead — `python-testing.md`
- No bare `except:` — always a specific exception type — `python-style.md`
- **No reading status back from Jira to reconcile, ever** — `get_sprint_issues`/`complete_sprint_if_done` read status only to decide the one-way "complete sprint" write, never to reconcile a story already moved by a human — spec Non-goals
- **No backward transitions** — enforced via `statusCategory` rank comparison, not transition-list absence (see correction #1 above) — spec Non-goals
- **No assignee, comment, or worklog writes** — unchanged from the 0.1.0 contract — spec Non-goals
- **No per-issue sprint assignment** — the only new sprint-level write is marking an already-fully-Done sprint complete — spec Non-goals
- `start-story`/`complete-story` run **silently, no confirmation prompt** — the git event (branch creation / PR merge) is the confirmation, per the user's explicit choice — spec Architecture
- A `JiraSyncError` from `transition_issue` must never block or roll back the git operation that triggered it — surface it to the user as a warning, don't fail the branch/merge — spec Error handling
- `resolve_issue_key_from_branch` returning `None` is silently swallowed by both hook call sites (no key in branch = nothing to do) — spec Hook points

---

## File Structure

**Package — `jira_sync_kit/` (existing repo at `~/Documents/GitHub/jira_sync_kit`, v0.1.0 → v0.2.0)**

```
jira_sync_kit/
  client.py          MODIFY — add get_transitions, transition_issue,
                      resolve_issue_key_from_branch (module-level),
                      get_active_sprint, get_sprint_issues,
                      get_issue_sprint_id, complete_sprint_if_done
  __main__.py         MODIFY — add start-story, complete-story subcommands
  __init__.py          MODIFY — __version__ = "0.2.0"
setup.py               MODIFY — version="0.2.0"
README.md              MODIFY — Status section
tests/
  test_status_automation.py   NEW — all new client methods + branch parsing
  test_main.py                 MODIFY — parser tests for the two new subcommands
```

**Template — `claude_project_template/`**

```
.claude/skills/commit-push/skill.md       MODIFY — branch-naming convention, start-story hook
.claude/skills/commit-push-pr/skill.md    MODIFY — complete-story hook after merge
.claude/skills/jira-log/SKILL.md          MODIFY — surface jira_key for branch naming
.claude/rules/jira-conventions.md         MODIFY — status-write exception, branch naming, confirm-before-write caveat
CLAUDE.md                                 MODIFY — one workflow line
```

**Sibling projects (propagated after the template is correct)**

```
job_search/                    requirements.txt pin bump + copy of the 5 template edits above
complaint_analyser/            copy of commit-push, commit-push-pr edits (jira-log/jira-conventions already exist, get the same edits)
cortex_signal_to_action/       same
playground/                    same
research_to_podcast/           same
tca/                           same
weather_forecaster/            same
```

---

## Task 1: `get_transitions` + `transition_issue` (client.py)

**Files:**
- Modify: `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/client.py` (append after `link_blocks`, currently ending line 251)
- Test: `~/Documents/GitHub/jira_sync_kit/tests/test_status_automation.py` (new file)

**Interfaces:**
- Consumes: `self._request`, `JiraSyncError` (both already in `client.py`)
- Produces: `JiraClient.get_transitions(issue_key: str) -> list[dict]`, `JiraClient.transition_issue(issue_key: str, target_status_name: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `~/Documents/GitHub/jira_sync_kit/tests/test_status_automation.py`:

```python
import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from jira_sync_kit.client import JiraClient
from jira_sync_kit.errors import JiraSyncError

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
class TestGetTransitions(unittest.TestCase):
    def setUp(self):
        self.client = make_client()
        self.project_key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")
        self.client.ensure_project(self.project_key, "ZZ Test Project")

    def test_lists_in_progress_and_done(self):
        key = self.client.create_issue(self.project_key, "Story", "Transitions test")
        names = {t["name"] for t in self.client.get_transitions(key)}
        self.assertIn("In Progress", names)
        self.assertIn("Done", names)


@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestTransitionIssue(unittest.TestCase):
    def setUp(self):
        self.client = make_client()
        self.project_key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")
        self.client.ensure_project(self.project_key, "ZZ Test Project")

    def test_forward_transition_returns_true(self):
        key = self.client.create_issue(self.project_key, "Story", "Forward test")
        self.assertTrue(self.client.transition_issue(key, "In Progress"))

    def test_repeat_transition_to_same_status_is_a_noop(self):
        key = self.client.create_issue(self.project_key, "Story", "Repeat test")
        self.client.transition_issue(key, "In Progress")
        self.assertFalse(self.client.transition_issue(key, "In Progress"))

    def test_backward_transition_is_a_noop_not_an_error(self):
        key = self.client.create_issue(self.project_key, "Story", "Backward test")
        self.client.transition_issue(key, "In Progress")
        self.assertFalse(self.client.transition_issue(key, "To Do"))

    def test_unreachable_status_raises(self):
        key = self.client.create_issue(self.project_key, "Story", "Unreachable test")
        with self.assertRaises(JiraSyncError):
            self.client.transition_issue(key, "Not A Real Status")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_status_automation -v`
Expected: FAIL / ERROR — `AttributeError: 'JiraClient' object has no attribute 'get_transitions'`

- [ ] **Step 3: Implement `get_transitions` and `transition_issue`**

In `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/client.py`, append to the `JiraClient` class (after `link_blocks`):

```python
    def get_transitions(self, issue_key: str) -> list[dict]:
        """List the transitions currently available from an issue's status.

        Args:
            issue_key: The issue to inspect, e.g. "JOB-16".

        Returns:
            The raw list of transition dicts from Jira, each with at least
            "id", "name", and "to" (the target status, including its
            statusCategory).
        """
        return self._request(
            "GET", f"/rest/api/3/issue/{issue_key}/transitions"
        ).json()["transitions"]

    _STATUS_CATEGORY_RANK = {"new": 0, "indeterminate": 1, "done": 2}

    def transition_issue(self, issue_key: str, target_status_name: str) -> bool:
        """Move an issue forward to `target_status_name`, never backward.

        Jira's transitions list is not reliably forward-only — on a
        default/simple workflow every status is offered as a transition
        from every other status. Forward-only is enforced here by
        comparing statusCategory rank (new < indeterminate < done) between
        the issue's current status and the matching transition's target,
        rather than trusting transition-list absence to mean "already
        there or past".

        Args:
            issue_key: The issue to transition, e.g. "JOB-16".
            target_status_name: The exact status name to move to, e.g.
                "In Progress" or "Done".

        Returns:
            True if a transition was performed. False if the issue is
            already at or past `target_status_name` (no-op).

        Raises:
            JiraSyncError: If no transition to `target_status_name` exists
                from the issue's current status at all.
        """
        current_status = self._request(
            "GET", f"/rest/api/3/issue/{issue_key}", params={"fields": "status"}
        ).json()["fields"]["status"]
        transitions = self.get_transitions(issue_key)
        match = next(
            (t for t in transitions if t["name"] == target_status_name), None
        )
        if match is None:
            raise JiraSyncError(
                f"{issue_key} has no transition to '{target_status_name}' "
                f"from its current status '{current_status['name']}'"
            )
        current_rank = self._STATUS_CATEGORY_RANK[
            current_status["statusCategory"]["key"]
        ]
        target_rank = self._STATUS_CATEGORY_RANK[match["to"]["statusCategory"]["key"]]
        if target_rank <= current_rank:
            return False
        self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": match["id"]}},
        )
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_status_automation -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Format and commit**

```bash
cd ~/Documents/GitHub/jira_sync_kit
black jira_sync_kit/client.py tests/test_status_automation.py
isort jira_sync_kit/client.py tests/test_status_automation.py
ruff check jira_sync_kit/client.py tests/test_status_automation.py
git add jira_sync_kit/client.py tests/test_status_automation.py
git commit -m "feat: add get_transitions and transition_issue, forward-only via statusCategory rank"
```

---

## Task 2: `resolve_issue_key_from_branch` (client.py, pure function)

**Files:**
- Modify: `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/client.py` (module-level, above the `JiraClient` class; needs `import re` added to imports)
- Test: `~/Documents/GitHub/jira_sync_kit/tests/test_status_automation.py`

**Interfaces:**
- Produces: `resolve_issue_key_from_branch(branch_name: str) -> str | None` (module-level function in `jira_sync_kit.client`, imported directly by `__main__.py` in Task 7/8)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_status_automation.py` (no network needed, no `skipUnless` gate):

```python
from jira_sync_kit.client import resolve_issue_key_from_branch


class TestResolveIssueKeyFromBranch(unittest.TestCase):
    def test_extracts_key_from_conventional_branch_name(self):
        self.assertEqual(
            resolve_issue_key_from_branch("feat/JOB-16-repo-scaffold"), "JOB-16"
        )

    def test_returns_none_for_branch_with_no_key(self):
        self.assertIsNone(resolve_issue_key_from_branch("feat/repo-scaffold"))

    def test_returns_none_for_malformed_key(self):
        self.assertIsNone(resolve_issue_key_from_branch("feat/job16-repo-scaffold"))

    def test_returns_none_for_main_branch(self):
        self.assertIsNone(resolve_issue_key_from_branch("main"))
```

(Add the `from jira_sync_kit.client import resolve_issue_key_from_branch` import at the top of the file alongside the existing `JiraClient` import, not inline as shown above.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_status_automation.TestResolveIssueKeyFromBranch -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_issue_key_from_branch'`

- [ ] **Step 3: Implement**

In `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/client.py`, add `import re` to the imports (standard library, before `import requests`), then add this module-level function above `class JiraClient:`:

```python
_BRANCH_JIRA_KEY_RE = re.compile(r"^[^/]+/([A-Z][A-Z0-9]+-\d+)-")


def resolve_issue_key_from_branch(branch_name: str) -> str | None:
    """Parse a Jira issue key out of a `<type>/<JIRA-KEY>-<slug>` branch name.

    Args:
        branch_name: A git branch name, e.g. "feat/JOB-16-repo-scaffold".

    Returns:
        The embedded Jira key (e.g. "JOB-16"), or None if the branch name
        doesn't match the convention — treated as "nothing to do", not an
        error, so pre-existing branches or projects without Jira tracking
        are unaffected.
    """
    match = _BRANCH_JIRA_KEY_RE.match(branch_name)
    return match.group(1) if match else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_status_automation.TestResolveIssueKeyFromBranch -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Format and commit**

```bash
cd ~/Documents/GitHub/jira_sync_kit
black jira_sync_kit/client.py tests/test_status_automation.py
isort jira_sync_kit/client.py tests/test_status_automation.py
git add jira_sync_kit/client.py tests/test_status_automation.py
git commit -m "feat: add resolve_issue_key_from_branch"
```

---

## Task 3: `get_active_sprint` + `get_sprint_issues` (client.py)

**Files:**
- Modify: `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/client.py`
- Test: `~/Documents/GitHub/jira_sync_kit/tests/test_status_automation.py`

**Interfaces:**
- Produces: `JiraClient.get_active_sprint(board_id: int) -> dict | None`, `JiraClient.get_sprint_issues(sprint_id: int) -> list[dict]`

**Verified shapes** (from live `ZZTEST` board, id 67):
- `GET /rest/agile/1.0/board/{boardId}/sprint?state=active` → `{"values": [{"id": 101, "state": "active", "name": "...", ...}], ...}` (empty `values` when none active)
- `GET /rest/agile/1.0/sprint/{sprintId}/issue?fields=status` → `{"total": N, "issues": [{"key": "ZZTEST-1", "fields": {"status": {"name": "To Do", "statusCategory": {"key": "new"}}}}]}`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_status_automation.py`:

```python
@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestSprintReads(unittest.TestCase):
    def setUp(self):
        self.client = make_client()
        self.project_key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")
        self.client.ensure_project(self.project_key, "ZZ Test Project")
        boards = self.client._request(
            "GET",
            "/rest/agile/1.0/board",
            params={"projectKeyOrId": self.project_key},
        ).json()["values"]
        self.board_id = boards[0]["id"]

    def _make_active_sprint(self, name: str) -> int:
        sprint = self.client._request(
            "POST",
            "/rest/agile/1.0/sprint",
            json={"name": name, "originBoardId": self.board_id},
        ).json()
        now = datetime.now(timezone.utc)
        self.client._request(
            "PUT",
            f"/rest/agile/1.0/sprint/{sprint['id']}",
            json={
                "name": name,
                "state": "active",
                "startDate": now.isoformat(),
                "endDate": (now + timedelta(days=7)).isoformat(),
            },
        )
        return sprint["id"]

    def test_get_active_sprint_returns_the_active_one(self):
        sprint_id = self._make_active_sprint(f"active-{time.time()}")
        active = self.client.get_active_sprint(self.board_id)
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], sprint_id)

    def test_get_sprint_issues_returns_added_issues(self):
        sprint_id = self._make_active_sprint(f"issues-{time.time()}")
        key = self.client.create_issue(self.project_key, "Story", "Sprint issue test")
        self.client._request(
            "POST",
            f"/rest/agile/1.0/sprint/{sprint_id}/issue",
            json={"issues": [key]},
        )
        issues = self.client.get_sprint_issues(sprint_id)
        self.assertIn(key, [i["key"] for i in issues])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_status_automation.TestSprintReads -v`
Expected: FAIL — `AttributeError: 'JiraClient' object has no attribute 'get_active_sprint'`

- [ ] **Step 3: Implement**

Append to the `JiraClient` class in `client.py`:

```python
    def get_active_sprint(self, board_id: int) -> dict | None:
        """Fetch the currently active sprint on a board, if any.

        Args:
            board_id: The Agile board id.

        Returns:
            The active sprint dict, or None if no sprint is active.
        """
        values = self._request(
            "GET",
            f"/rest/agile/1.0/board/{board_id}/sprint",
            params={"state": "active"},
        ).json()["values"]
        return values[0] if values else None

    def get_sprint_issues(self, sprint_id: int) -> list[dict]:
        """List every issue currently in a sprint, with status.

        Args:
            sprint_id: The Agile sprint id.

        Returns:
            All issues in the sprint (paginated internally), each with at
            least "key" and "fields"["status"].
        """
        issues: list[dict] = []
        start_at = 0
        while True:
            page = self._request(
                "GET",
                f"/rest/agile/1.0/sprint/{sprint_id}/issue",
                params={"fields": "status", "startAt": start_at},
            ).json()
            issues.extend(page["issues"])
            start_at += len(page["issues"])
            if start_at >= page["total"] or not page["issues"]:
                break
        return issues
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_status_automation.TestSprintReads -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Format and commit**

```bash
cd ~/Documents/GitHub/jira_sync_kit
black jira_sync_kit/client.py tests/test_status_automation.py
isort jira_sync_kit/client.py tests/test_status_automation.py
git add jira_sync_kit/client.py tests/test_status_automation.py
git commit -m "feat: add get_active_sprint and get_sprint_issues"
```

---

## Task 4: `get_issue_sprint_id` + `complete_sprint_if_done` (client.py)

**Files:**
- Modify: `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/client.py`
- Test: `~/Documents/GitHub/jira_sync_kit/tests/test_status_automation.py`

**Interfaces:**
- Consumes: `get_sprint_issues` (Task 3)
- Produces: `JiraClient.get_issue_sprint_id(issue_key: str) -> int | None`, `JiraClient.complete_sprint_if_done(sprint_id: int) -> bool`

**Verified shapes:**
- `GET /rest/agile/1.0/issue/{key}` → top-level `fields.sprint` = `{"id": 101, "state": "active", "name": "...", "boardId": 67, ...}`, or `fields.sprint` absent/`None` when the issue isn't in a sprint.
- Completing: `GET /rest/agile/1.0/sprint/{id}` → `{"name": ..., "startDate": ..., "endDate": ..., "state": "active", ...}`, then `PUT /rest/agile/1.0/sprint/{id}` with `{"name": <same>, "state": "closed", "startDate": <same>, "endDate": <same>}` → 200, `"completeDate"` set by Jira automatically. A partial body (e.g. `{"state": "closed"}` alone) 400s with `"Sprint name is required"` / `"You must specify a start date"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_status_automation.py` (reuses `TestSprintReads`'s `_make_active_sprint` pattern — put these in the same class, or duplicate the small helper into a new class; shown here as a new class for isolation):

```python
@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestCompleteSprintIfDone(unittest.TestCase):
    def setUp(self):
        self.client = make_client()
        self.project_key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")
        self.client.ensure_project(self.project_key, "ZZ Test Project")
        boards = self.client._request(
            "GET",
            "/rest/agile/1.0/board",
            params={"projectKeyOrId": self.project_key},
        ).json()["values"]
        self.board_id = boards[0]["id"]

    def _make_active_sprint(self, name: str) -> int:
        sprint = self.client._request(
            "POST",
            "/rest/agile/1.0/sprint",
            json={"name": name, "originBoardId": self.board_id},
        ).json()
        now = datetime.now(timezone.utc)
        self.client._request(
            "PUT",
            f"/rest/agile/1.0/sprint/{sprint['id']}",
            json={
                "name": name,
                "state": "active",
                "startDate": now.isoformat(),
                "endDate": (now + timedelta(days=7)).isoformat(),
            },
        )
        return sprint["id"]

    def test_noop_when_an_issue_is_still_open(self):
        sprint_id = self._make_active_sprint(f"open-{time.time()}")
        key = self.client.create_issue(self.project_key, "Story", "Still open")
        self.client._request(
            "POST",
            f"/rest/agile/1.0/sprint/{sprint_id}/issue",
            json={"issues": [key]},
        )
        self.assertFalse(self.client.complete_sprint_if_done(sprint_id))

    def test_completes_when_every_issue_is_done(self):
        sprint_id = self._make_active_sprint(f"done-{time.time()}")
        key = self.client.create_issue(self.project_key, "Story", "All done")
        self.client._request(
            "POST",
            f"/rest/agile/1.0/sprint/{sprint_id}/issue",
            json={"issues": [key]},
        )
        self.client.transition_issue(key, "Done")

        self.assertTrue(self.client.complete_sprint_if_done(sprint_id))

        sprint = self.client._request(
            "GET", f"/rest/agile/1.0/sprint/{sprint_id}"
        ).json()
        self.assertEqual(sprint["state"], "closed")

    def test_noop_when_sprint_has_no_issues(self):
        sprint_id = self._make_active_sprint(f"empty-{time.time()}")
        self.assertFalse(self.client.complete_sprint_if_done(sprint_id))


@unittest.skipUnless(_HAS_CREDS, "JIRA_SITE_URL/JIRA_EMAIL/JIRA_API_TOKEN not set")
class TestGetIssueSprintId(unittest.TestCase):
    def setUp(self):
        self.client = make_client()
        self.project_key = os.environ.get("JIRA_TEST_PROJECT_KEY", "ZZTEST")
        self.client.ensure_project(self.project_key, "ZZ Test Project")

    def test_returns_none_when_issue_not_in_a_sprint(self):
        key = self.client.create_issue(self.project_key, "Story", "No sprint")
        self.assertIsNone(self.client.get_issue_sprint_id(key))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_status_automation.TestCompleteSprintIfDone tests.test_status_automation.TestGetIssueSprintId -v`
Expected: FAIL — `AttributeError: 'JiraClient' object has no attribute 'complete_sprint_if_done'`

- [ ] **Step 3: Implement**

Append to the `JiraClient` class in `client.py`:

```python
    def get_issue_sprint_id(self, issue_key: str) -> int | None:
        """Resolve the sprint an issue currently belongs to, if any.

        Not in the original design spec's method table, but required to
        fulfil its documented CLI behaviour ("complete-story ... using the
        board/sprint resolved from the issue's own sprint field") — the
        Agile issue endpoint exposes a top-level "sprint" field directly,
        so no separate board lookup is needed.

        Args:
            issue_key: The issue to inspect, e.g. "JOB-16".

        Returns:
            The sprint id, or None if the issue isn't in a sprint.
        """
        sprint = (
            self._request("GET", f"/rest/agile/1.0/issue/{issue_key}")
            .json()["fields"]
            .get("sprint")
        )
        return sprint["id"] if sprint else None

    def complete_sprint_if_done(self, sprint_id: int) -> bool:
        """Complete a sprint, but only if every issue in it is Done.

        Args:
            sprint_id: The Agile sprint id.

        Returns:
            True if the sprint was completed. False if it has no issues,
            or at least one issue isn't Done yet (no partial action).
        """
        issues = self.get_sprint_issues(sprint_id)
        if not issues:
            return False
        if not all(
            issue["fields"]["status"]["statusCategory"]["key"] == "done"
            for issue in issues
        ):
            return False
        sprint = self._request("GET", f"/rest/agile/1.0/sprint/{sprint_id}").json()
        self._request(
            "PUT",
            f"/rest/agile/1.0/sprint/{sprint_id}",
            json={
                "name": sprint["name"],
                "state": "closed",
                "startDate": sprint["startDate"],
                "endDate": sprint["endDate"],
            },
        )
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_status_automation.TestCompleteSprintIfDone tests.test_status_automation.TestGetIssueSprintId -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full new test file, then format and commit**

```bash
cd ~/Documents/GitHub/jira_sync_kit
python3.11 -m unittest tests.test_status_automation -v
black jira_sync_kit/client.py tests/test_status_automation.py
isort jira_sync_kit/client.py tests/test_status_automation.py
ruff check jira_sync_kit/client.py tests/test_status_automation.py
git add jira_sync_kit/client.py tests/test_status_automation.py
git commit -m "feat: add get_issue_sprint_id and complete_sprint_if_done"
```

Expected: all tests in `test_status_automation.py` pass (15 tests total across Tasks 1-4).

---

## Task 5: CLI `start-story` and `complete-story` subcommands (__main__.py)

**Files:**
- Modify: `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/__main__.py`
- Test: `~/Documents/GitHub/jira_sync_kit/tests/test_main.py` (existing file — parser-level unit tests, no network, matching its established pattern)

**Interfaces:**
- Consumes: `JiraClient.transition_issue`, `JiraClient.get_issue_sprint_id`, `JiraClient.complete_sprint_if_done` (Tasks 1, 4), `resolve_issue_key_from_branch` (Task 2)
- Produces: `python -m jira_sync_kit start-story [--branch BRANCH] [--dry-run]`, `python -m jira_sync_kit complete-story [--branch BRANCH] [--dry-run]`

- [ ] **Step 1: Write the failing tests**

Read `~/Documents/GitHub/jira_sync_kit/tests/test_main.py` first to match its existing import and class style exactly, then append:

```python
class TestStartStoryParser(unittest.TestCase):
    def test_branch_defaults_to_none(self):
        args = build_parser().parse_args(["start-story"])
        self.assertIsNone(args.branch)
        self.assertFalse(args.dry_run)

    def test_accepts_explicit_branch_and_dry_run(self):
        args = build_parser().parse_args(
            ["start-story", "--branch", "feat/JOB-1-x", "--dry-run"]
        )
        self.assertEqual(args.branch, "feat/JOB-1-x")
        self.assertTrue(args.dry_run)


class TestCompleteStoryParser(unittest.TestCase):
    def test_branch_defaults_to_none(self):
        args = build_parser().parse_args(["complete-story"])
        self.assertIsNone(args.branch)
        self.assertFalse(args.dry_run)

    def test_accepts_explicit_branch_and_dry_run(self):
        args = build_parser().parse_args(
            ["complete-story", "--branch", "feat/JOB-1-x", "--dry-run"]
        )
        self.assertEqual(args.branch, "feat/JOB-1-x")
        self.assertTrue(args.dry_run)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_main.TestStartStoryParser tests.test_main.TestCompleteStoryParser -v`
Expected: FAIL — `SystemExit` / argparse error, "invalid choice: 'start-story'"

- [ ] **Step 3: Implement**

In `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/__main__.py`:

Add `import subprocess` to the standard-library imports (alphabetical, after `shutil`).

Change the import line:
```python
from jira_sync_kit.client import JiraClient
```
to:
```python
from jira_sync_kit.client import JiraClient, resolve_issue_key_from_branch
```

In `build_parser()`, after the `init_parser` block (before `return parser`):

```python
    start_story = subparsers.add_parser(
        "start-story", help="Transition a branch's Jira issue to In Progress"
    )
    start_story.add_argument("--branch", default=None)
    start_story.add_argument(
        "--dry-run", action="store_true", help="Print what would happen, do nothing"
    )

    complete_story = subparsers.add_parser(
        "complete-story",
        help="Transition a branch's Jira issue to Done, "
        "complete its sprint if every issue in it is Done",
    )
    complete_story.add_argument("--branch", default=None)
    complete_story.add_argument(
        "--dry-run", action="store_true", help="Print what would happen, do nothing"
    )
```

After `_require_existing_project` (before `def main`), add:

```python
def _current_branch() -> str:
    """Return the current git branch name.

    Returns:
        The current branch name, e.g. "feat/JOB-16-repo-scaffold".
    """
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
```

In `main()`, after the existing `elif args.command == "sync":` block, add:

```python
    elif args.command == "start-story":
        branch = args.branch or _current_branch()
        key = resolve_issue_key_from_branch(branch)
        if key is None:
            print(f"No Jira key in branch '{branch}' — nothing to do.")
            return
        if args.dry_run:
            print(f"[dry-run] Would transition {key} to 'In Progress'")
            return
        moved = client.transition_issue(key, "In Progress")
        print(
            f"{key}: "
            f"{'moved to In Progress' if moved else 'already In Progress or further — no-op'}"
        )
    elif args.command == "complete-story":
        branch = args.branch or _current_branch()
        key = resolve_issue_key_from_branch(branch)
        if key is None:
            print(f"No Jira key in branch '{branch}' — nothing to do.")
            return
        if args.dry_run:
            print(
                f"[dry-run] Would transition {key} to 'Done' "
                "and check sprint completion"
            )
            return
        moved = client.transition_issue(key, "Done")
        print(
            f"{key}: {'moved to Done' if moved else 'already Done or further — no-op'}"
        )
        sprint_id = client.get_issue_sprint_id(key)
        if sprint_id is not None and client.complete_sprint_if_done(sprint_id):
            print(f"Sprint {sprint_id} completed — every issue in it is Done.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -m unittest tests.test_main -v`
Expected: PASS (all `test_main.py` tests, including the 4 new ones)

- [ ] **Step 5: Manual dry-run verification against the real CLI**

```bash
cd ~/Documents/GitHub/jira_sync_kit
python3.11 -m jira_sync_kit start-story --branch feat/ZZTEST-1-manual-check --dry-run
python3.11 -m jira_sync_kit start-story --branch chore/no-ticket --dry-run
```
Expected: first prints `[dry-run] Would transition ZZTEST-1 to 'In Progress'`; second prints `No Jira key in branch 'chore/no-ticket' — nothing to do.`

- [ ] **Step 6: Format and commit**

```bash
cd ~/Documents/GitHub/jira_sync_kit
black jira_sync_kit/__main__.py tests/test_main.py
isort jira_sync_kit/__main__.py tests/test_main.py
ruff check jira_sync_kit/__main__.py tests/test_main.py
git add jira_sync_kit/__main__.py tests/test_main.py
git commit -m "feat: add start-story and complete-story CLI subcommands"
```

---

## Task 6: Version bump + README

**Files:**
- Modify: `~/Documents/GitHub/jira_sync_kit/jira_sync_kit/__init__.py`
- Modify: `~/Documents/GitHub/jira_sync_kit/setup.py`
- Modify: `~/Documents/GitHub/jira_sync_kit/README.md`

**Interfaces:**
- Consumes: nothing new (housekeeping only)
- Produces: `jira_sync_kit.__version__ == "0.2.0"`, matching `setup.py`'s `version=`

- [ ] **Step 1: Bump the version in both places**

In `jira_sync_kit/__init__.py`, change:
```python
__version__ = "0.1.0"
```
to:
```python
__version__ = "0.2.0"
```

In `setup.py`, change `version="0.1.0"` to `version="0.2.0"`.

- [ ] **Step 2: Verify they match**

Run: `cd ~/Documents/GitHub/jira_sync_kit && python3.11 -c "import jira_sync_kit; print(jira_sync_kit.__version__)"`
Expected: `0.2.0`

- [ ] **Step 3: Update the README's Status section**

Read the current `## Status` section of `README.md` first, then add a line noting the new status-automation capability (`start-story`/`complete-story`, forward-only `status` writes triggered by git events) alongside the existing "Implemented: ..." line, following the file's existing prose style — don't reformat unrelated sections.

- [ ] **Step 4: Run the full test suite**

Run: `cd ~/Documents/GitHub/jira_sync_kit && coverage run -m unittest discover && coverage report -m`
Expected: all tests pass (existing + new)

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/GitHub/jira_sync_kit
git add jira_sync_kit/__init__.py setup.py README.md
git commit -m "chore: bump version to 0.2.0"
```

---

## Task 7: `commit-push` skill — branch-naming convention + `start-story` hook

**Files:**
- Modify: `claude_project_template/.claude/skills/commit-push/skill.md`

**Interfaces:**
- Consumes: `python -m jira_sync_kit start-story --branch <name>` (Task 5), `jira_key` surfaced by `jira-log` (Task 9)
- Produces: updated Step 1 in the skill, consumed by every project that copies it (Task 12)

- [ ] **Step 1: Edit Step 1.2 (branch-name selection)**

In `claude_project_template/.claude/skills/commit-push/skill.md`, replace:

```markdown
2. Pick a branch name: `<type>/<slug>` — `type` is a Conventional-Commit-style prefix (`feat`, `fix`, `chore`, `docs`, `refactor`, `test`) matching the change; `slug` is a short kebab-case description (3-5 words) inferred from the task or the diff. Example: `feat/add-retry-logic`.
3. Create and switch to it: `git checkout -b <type>/<slug>`.
```

with:

```markdown
2. Pick a branch name. If `plan/backlog.yml` exists in this project and the `jira-log` skill resolved a `jira_key` for this change earlier in the workflow, use `<type>/<JIRA-KEY>-<slug>` (e.g. `feat/JOB-16-repo-scaffold`). Otherwise — no `plan/backlog.yml`, or no resolved key (e.g. a `chore`/`docs` change with no ticket) — use `<type>/<slug>` as before. `type` is a Conventional-Commit-style prefix (`feat`, `fix`, `chore`, `docs`, `refactor`, `test`) matching the change; `slug` is a short kebab-case description (3-5 words) inferred from the task or the diff.
3. Create and switch to it: `git checkout -b <branch-name>`.
4. If the branch name matches `<type>/<JIRA-KEY>-<slug>`, silently run:
   ```bash
   python -m jira_sync_kit start-story --branch <branch-name>
   ```
   Swallow a "No Jira key in branch ... — nothing to do" result silently (expected for branches without a resolved key). If the command exits non-zero for any other reason, report the failure to the user as a warning — do not undo the branch creation, it already succeeded.
```

- [ ] **Step 2: Read the edited file back and confirm it renders correctly**

Read `claude_project_template/.claude/skills/commit-push/skill.md` in full and confirm Step 1 now has 4 numbered sub-steps and no leftover reference to the old unconditional `<type>/<slug>` wording.

- [ ] **Step 3: Commit**

```bash
git add claude_project_template/.claude/skills/commit-push/skill.md
git commit -m "feat(claude-skills): wire start-story into commit-push branch creation"
```

(Note: this repo's git object store is corrupted per `CLAUDE.local.md` — commit and push via the clean clone at `/tmp/fresh_portfolio`, following that file's documented rsync workflow, not directly from this working copy.)

---

## Task 8: `commit-push-pr` skill — `complete-story` hook after merge

**Files:**
- Modify: `claude_project_template/.claude/skills/commit-push-pr/skill.md`

**Interfaces:**
- Consumes: `python -m jira_sync_kit complete-story --branch <name>` (Task 5), the branch name captured during Step 1 (before `--delete-branch` removes it)

- [ ] **Step 1: Edit Step 3 (merge and clean up)**

In `claude_project_template/.claude/skills/commit-push-pr/skill.md`, replace:

```markdown
3. **Merge:** `gh pr merge --squash --delete-branch`. Squash keeps `main`'s history linear; use a different merge method only if the user asks for one.
4. **Sync back:** `git checkout main && git pull`.
```

with:

```markdown
3. **Merge:** `gh pr merge --squash --delete-branch`. Squash keeps `main`'s history linear; use a different merge method only if the user asks for one.
4. **Complete the Jira story, if tracked.** If the branch merged in step 3 matched `<type>/<JIRA-KEY>-<slug>` (per `commit-push`'s Step 1), silently run:
   ```bash
   python -m jira_sync_kit complete-story --branch <branch-name>
   ```
   using the branch name captured before the merge — `--delete-branch` removes it, so don't rely on `git rev-parse --abbrev-ref HEAD` here. Swallow a "No Jira key in branch ... — nothing to do" result silently. If the command exits non-zero for any other reason, report the failure to the user as a warning — the merge already succeeded, don't roll it back.
5. **Sync back:** `git checkout main && git pull`.
```

- [ ] **Step 2: Read the edited file back and confirm the numbering is consistent**

Read `claude_project_template/.claude/skills/commit-push-pr/skill.md` in full and confirm Step 3 now has 5 numbered sub-steps in the right order (merge → complete-story → sync back).

- [ ] **Step 3: Commit**

```bash
git add claude_project_template/.claude/skills/commit-push-pr/skill.md
git commit -m "feat(claude-skills): wire complete-story into commit-push-pr merge step"
```

(Same note as Task 7 — commit/push via `/tmp/fresh_portfolio`.)

---

## Task 9: `jira-log` skill — surface `jira_key` for branch naming

**Files:**
- Modify: `claude_project_template/.claude/skills/jira-log/SKILL.md`

**Interfaces:**
- Produces: the resolved `jira_key`, available to `commit-push`'s Step 1.2 (Task 7) later in the same session

- [ ] **Step 1: Edit the "On a confirmed `feat`" section**

In `claude_project_template/.claude/skills/jira-log/SKILL.md`, replace:

```markdown
4. Report the created issue key(s) back to the user.
```

(the one under "## On a confirmed `feat`", not the "On a confirmed `fix`" one above it) with:

```markdown
4. Report the created issue key(s) back to the user, and keep the story's `jira_key` available for this session — if a branch for this work hasn't been created yet, `commit-push`'s branch-naming step reuses it rather than re-deriving which story this is.
```

- [ ] **Step 2: Read the edited file back to confirm only the `feat` section changed**

Read `claude_project_template/.claude/skills/jira-log/SKILL.md` in full; confirm the "On a confirmed `fix`" section's step 4 is untouched and the "Never" section (including "Read or write Jira's status ... or sprint fields") is untouched — this skill itself still never touches status.

- [ ] **Step 3: Commit**

```bash
git add claude_project_template/.claude/skills/jira-log/SKILL.md
git commit -m "docs(jira-log): surface jira_key for commit-push branch naming"
```

---

## Task 10: `jira-conventions.md` — status exception, branch naming, confirm-before-write caveat

**Files:**
- Modify: `claude_project_template/.claude/rules/jira-conventions.md`

- [ ] **Step 1: Add the status-write exception after the field-ownership table**

After the "## Field ownership" table and its existing note about `acceptance`, add:

```markdown
**Exception, added in `jira_sync_kit` 0.2.0:** `jira_sync_kit start-story`/`complete-story`
may write `status` forward-only, triggered by git branch-creation/PR-merge events — never
by session judgment, and never with a confirmation prompt (see
`docs/superpowers/specs/2026-08-28-jira-status-automation-design.md`). This does not make
status bidirectional: `jira_sync_kit` still never reads status back from Jira to reconcile,
never moves it backward, and a human moving a card in the Jira UI is never overwritten.
```

- [ ] **Step 2: Add a caveat to "## Confirm-before-write"**

After the existing paragraph in that section, add:

```markdown
**Exception:** the narrow, git-event-triggered status writes (`start-story`/`complete-story`,
and the sprint-completion check they trigger) are silent by design — the triggering event
(branch creation, PR merge) is itself the confirmation. This applies only to those two
automated calls, never to a drafted Bug or Story.
```

- [ ] **Step 3: Add a new "## Branch naming" section**

Add after "## Idempotency" and before "## Confirm-before-write" (or at the end of the file — either position is fine, place it near the other structural conventions):

```markdown
## Branch naming

If `plan/backlog.yml` exists, branches for tracked stories use
`<type>/<JIRA-KEY>-<slug>` (e.g. `feat/JOB-16-repo-scaffold`) instead of the plain
`<type>/<slug>`. Pre-existing branches created before this convention simply have no
embedded Jira key — `resolve_issue_key_from_branch` returns `None` for them, treated as a
silent no-op. No migration is needed.
```

- [ ] **Step 4: Read the edited file back in full**

Confirm all three edits are present, correctly placed, and the rest of the file (issue-type mapping table) is untouched.

- [ ] **Step 5: Commit**

```bash
git add claude_project_template/.claude/rules/jira-conventions.md
git commit -m "docs(jira-conventions): document status-write exception and branch naming"
```

---

## Task 11: `CLAUDE.md` — workflow line

**Files:**
- Modify: `claude_project_template/CLAUDE.md`

- [ ] **Step 1: Edit Development Workflow step 1**

Replace:
```markdown
Branch as `<type>/<slug>` (see the `commit-push` skill).
```
with:
```markdown
Branch as `<type>/<slug>`, or `<type>/<JIRA-KEY>-<slug>` if `plan/backlog.yml` exists and `jira-log` resolved a key (see the `commit-push` skill).
```

- [ ] **Step 2: Commit**

```bash
git add claude_project_template/CLAUDE.md
git commit -m "docs(claude-md): note Jira-key branch naming in workflow step 1"
```

---

## Task 12: Release `jira_sync_kit` 0.2.0

**Files:** none (git tag + push in the separate `jira_sync_kit` repo)

**⚠️ This task pushes a new tag to a private GitHub repo — confirm with the user before running Step 1.** It publishes a version other projects can pull.

- [ ] **Step 1: Confirm all tests pass one more time**

```bash
cd ~/Documents/GitHub/jira_sync_kit
coverage run -m unittest discover
coverage report -m
```
Expected: all pass, no regressions from Tasks 1-6.

- [ ] **Step 2: Tag and push (after user confirmation)**

```bash
cd ~/Documents/GitHub/jira_sync_kit
git tag 0.2.0 -m "Forward-only status writes + sprint completion (start-story/complete-story)"
git push origin 0.2.0 && git push
```

---

## Task 13: `job_search` adoption

**Files:**
- Modify: `job_search/requirements.txt`
- Copy: `claude_project_template/.claude/skills/{commit-push,commit-push-pr,jira-log}/{skill.md,SKILL.md}` → `job_search/.claude/skills/...`
- Copy: `claude_project_template/.claude/rules/jira-conventions.md` → `job_search/.claude/rules/jira-conventions.md`
- Modify: `job_search/CLAUDE.md` (same workflow-line edit as Task 11)

- [ ] **Step 1: Bump the pinned version**

In `job_search/requirements.txt`, change:
```
git+https://github.com/FredGH/jira_sync_kit.git@0.1.0
```
to:
```
git+https://github.com/FredGH/jira_sync_kit.git@0.2.0
```

- [ ] **Step 2: Reinstall in job_search's venv**

```bash
cd job_search
source venv/bin/activate
pip3.11 install -r requirements.txt --upgrade
python3.11 -c "import jira_sync_kit; print(jira_sync_kit.__version__)"
```
Expected: `0.2.0`

- [ ] **Step 3: Copy the four edited template files**

Copy the exact post-edit contents of these four files from `claude_project_template/` into `job_search/`, matching the equivalent path:
- `.claude/skills/commit-push/skill.md`
- `.claude/skills/commit-push-pr/skill.md`
- `.claude/skills/jira-log/SKILL.md`
- `.claude/rules/jira-conventions.md`

Diff each pair afterward to confirm they're now byte-identical (matching the existing convention — the earlier `diff` check on `commit-push`/`jira-log` between `job_search` and `claude_project_template` showed 0 differences before this plan's edits, and should again after).

- [ ] **Step 4: Apply the same CLAUDE.md workflow-line edit as Task 11**

Same edit as Task 11, applied to `job_search/CLAUDE.md`'s equivalent line.

- [ ] **Step 5: Real-sync smoke test**

On a real feature branch in `job_search` with a story that has a `jira_key` already synced (e.g. reuse an existing `JOB-*` key from `plan/backlog.yml`), manually run:
```bash
cd job_search
python3.11 -m jira_sync_kit start-story --branch feat/JOB-<existing-key>-smoke-test --dry-run
```
Expected: `[dry-run] Would transition JOB-<existing-key> to 'In Progress'` — confirms the installed 0.2.0 package and the branch-parsing convention work together in this project's real environment.

- [ ] **Step 6: Commit**

```bash
git add job_search/requirements.txt job_search/.claude job_search/CLAUDE.md
git commit -m "chore(job_search): adopt jira_sync_kit 0.2.0 status automation"
```

(Via `/tmp/fresh_portfolio`, per `CLAUDE.local.md`.)

---

## Task 14: Propagate to remaining sibling projects

**Files:**
- Copy `claude_project_template/.claude/skills/{commit-push,commit-push-pr}/skill.md` into each of: `complaint_analyser/`, `cortex_signal_to_action/`, `playground/`, `research_to_podcast/`, `tca/`, `weather_forecaster/`
- Copy `claude_project_template/.claude/rules/jira-conventions.md` into the same six projects (already have the file from the prior propagation commit — this updates its content)
- Modify each project's `CLAUDE.md` (same one-line edit as Task 11), where the equivalent line exists

None of these six projects has a `plan/backlog.yml` yet, so — like the prior propagation commit — this is inert documentation until one of them adopts Jira tracking; the branch-naming and hook logic are explicitly conditional on that file existing.

- [ ] **Step 1: Copy the two skill files into all six projects**

For each of `complaint_analyser`, `cortex_signal_to_action`, `playground`, `research_to_podcast`, `tca`, `weather_forecaster`, overwrite:
- `<project>/.claude/skills/commit-push/skill.md` with the Task 7 version
- `<project>/.claude/skills/commit-push-pr/skill.md` with the Task 8 version
- `<project>/.claude/rules/jira-conventions.md` with the Task 10 version

- [ ] **Step 2: Apply the CLAUDE.md workflow-line edit where applicable**

Apply the same edit as Task 11 to each project's `CLAUDE.md`, matching how the prior propagation commit handled per-project variation (e.g. `cortex_signal_to_action/CLAUDE.md` needed slightly different surrounding context — check each file's current wording before editing rather than assuming they're identical).

- [ ] **Step 3: Verify consistency**

```bash
for p in complaint_analyser cortex_signal_to_action playground research_to_podcast tca weather_forecaster; do
  echo "=== $p ==="
  diff claude_project_template/.claude/skills/commit-push/skill.md "$p/.claude/skills/commit-push/skill.md"
  diff claude_project_template/.claude/skills/commit-push-pr/skill.md "$p/.claude/skills/commit-push-pr/skill.md"
  diff claude_project_template/.claude/rules/jira-conventions.md "$p/.claude/rules/jira-conventions.md"
done
```
Expected: no output (all diffs empty) for every project.

- [ ] **Step 4: Commit**

```bash
git add complaint_analyser cortex_signal_to_action playground research_to_podcast tca weather_forecaster
git commit -m "feat(claude-skills): propagate status-automation hooks to remaining sibling projects"
```

(Via `/tmp/fresh_portfolio`, per `CLAUDE.local.md`.)

---

## Post-implementation cleanup note

This plan's prerequisite research left live artifacts in the real `ZZTEST` Jira project (created it, plus `ZZTEST-1`/`ZZTEST-2` issues, a since-closed sprint, and a disposable sprint 102) — consistent with this codebase's existing test convention of never tearing down `ZZTEST` fixtures. No action needed; each task's own tests create their own disposable sprints/issues the same way.
