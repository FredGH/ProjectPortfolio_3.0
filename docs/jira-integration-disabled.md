# Jira integration — disabled (2026-09-26)

The Jira subscription ended, so the Jira integration is switched off in every
project under this repo. Nothing was deleted: everything needed to switch it
back on is still here.

## What was changed

Applied identically to all eight projects (`claude_project_template`,
`complaint_analyser`, `cortex_signal_to_action`, `job_search`, `playground`,
`research_to_podcast`, `tca`, `weather_forecaster`):

| Was | Now |
|---|---|
| `<project>/.claude/skills/jira-log/` | moved to `<project>/.claude/skills-disabled/jira-log/` (no longer discovered) |
| `<project>/.claude/rules/jira-conventions.md` | moved to `<project>/.claude/rules-disabled/jira-conventions.md` (no longer auto-loaded) |
| `CLAUDE.md` workflow steps 1–2 | branch is always `<type>/<slug>`; step 2 says Jira is disabled |
| `commit-push` skill, steps 1.2 and 1.4 | no `<JIRA-KEY>` in branch names; no `start-story` call |
| `commit-push-pr` skill, step 3.4 | no `complete-story` call |
| root `README.md`, `claude_project_template/README.md` | one-line "currently disabled" notes |

## What was deliberately left alone

- `job_search/plan/backlog.yml` and every `jira_key` in it — still the plan file,
  and re-syncing later depends on those keys (a story with a key is updated in
  place, never recreated).
- `jira_sync_kit` in `job_search/requirements-dev.txt` — a dev-only dependency,
  harmless while unused.
- `.env.example` Jira variables, and your real `.env` credentials.
- Historical specs and plans under `docs/superpowers/` that describe the
  integration's design.
- Existing branches that carry a `JOB-*` key in their name.

No hook, CI workflow, or user-level setting ever called Jira automatically — it
was driven entirely by the skill / rule / `CLAUDE.md` instructions above.

## How to re-enable

Simplest — undo the whole change with one revert:

```bash
git log --oneline --grep "disable Jira"     # find the commit
git revert <that-sha>                       # restores every file above
```

Then, once the subscription is active again:

1. Put real values for `JIRA_SITE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` in each
   project's `.env` (names are in `.env.example`).
2. Make sure `jira_sync_kit` is installed (`pip install -r requirements-dev.txt`
   in `job_search`; it is a private repo, so git credentials are needed).
3. `python -m jira_sync_kit sync` from `job_search` to reconcile
   `plan/backlog.yml` with Jira. Work done while Jira was off has no tickets —
   add them to the backlog by hand if you want them tracked.

To re-enable only some projects, `git mv` the two folders back for just those
projects instead of reverting, and restore that project's `CLAUDE.md` lines and
skill steps from the reverted commit's diff.
