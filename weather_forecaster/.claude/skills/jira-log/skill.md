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

**Known limitation:** the underlying sync only creates a subtask when its
parent story is *newly created* in the same sync run. If the story this
subtask belongs to was already synced in an earlier run, appending the
subtask to `backlog.yml` and re-running `sync` is currently a silent
no-op — nothing is created, no error. Tell the user this explicitly if
you detect it (the story already has a `jira_key` in `backlog.yml`),
rather than reporting success.

## Never

- Create anything without showing the draft and getting confirmation first.
- Auto-file from application logs — this skill only fires from an
  explicit session-time classification, never from log scanning.
- Read or write Jira's status, assignee, comments, worklog, or sprint fields.
