# CLAUDE.local.md

Personal overrides — gitignored, not shared with the team.

## Local Setup

- Python: 3.11
- Virtual env: `.venv/`
- Local project path: `/Users/fredericmarechal/Documents/GitHub/courses/data-eng-courses/ai-engineering-courses/ai-agentic-eng-course/projects/agents/projects/ProjectPortfolio_3.0/complaint_analyser`
- GitHub repo: `FredGH/ProjectPortfolio_3.0`

## Git Push Workflow

The local git object store is corrupted. **Never commit or push directly from the local repo.**
Always push via the clean clone at `/tmp/fresh_portfolio`:

```bash
LOCAL_CA="/Users/fredericmarechal/Documents/GitHub/courses/data-eng-courses/ai-engineering-courses/ai-agentic-eng-course/projects/agents/projects/ProjectPortfolio_3.0/complaint_analyser"
FRESH="/tmp/fresh_portfolio"

# Sync changed dirs (adjust paths to what actually changed)
rsync -av \
  --exclude='venv/' --exclude='.venv/' --exclude='*.egg-info/' \
  --exclude='__pycache__/' --exclude='*.pyc' --exclude='data/' \
  --exclude='.coverage' --exclude='dist/' --exclude='.ruff_cache/' \
  "$LOCAL_CA/<changed-dir>/" "$FRESH/complaint_analyser/<changed-dir>/"

# Commit and push
cd /tmp/fresh_portfolio
git add complaint_analyser/
git commit -m "..."
git fetch origin && git rebase origin/main && git push origin main
```

If `/tmp/fresh_portfolio` is missing, re-clone:
```bash
git clone https://github.com/FredGH/ProjectPortfolio_3.0.git /tmp/fresh_portfolio
```

## Personal Preferences

- Concise responses — no trailing summaries
- File references as clickable markdown links (`[file.py](path/to/file.py)`)

## Autonomous CI/CD Correction

When CI fails on the `complaint-analyser CI` workflow, auto-correct without waiting:

1. Poll the latest run:
   ```
   https://api.github.com/repos/FredGH/ProjectPortfolio_3.0/actions/runs?per_page=5
   ```
2. If `in_progress` — schedule a wakeup in 90 s and wait.
3. If `failure` — identify the failing job, then reproduce it locally by running the exact CI commands from `complaint_analyser/` as the working directory:

   | Job | Command |
   |---|---|
   | `lint` | `ruff check . && isort --check . && black --check .` |
   | `test` | `coverage run -m unittest discover -s tests && coverage report -m --fail-under=80` |
   | `validate-configs` | `python scripts/validate_configs.py && docker compose config --quiet` |
   | `evaluate` | `python evaluation/eval.py --dataset evaluation/golden_dataset.json --f1-threshold 0.80 --faithfulness-threshold 0.75` |

4. Fix the errors:
   - **Formatting** (isort/black): run `isort . && black .` in place, then sync all changed files.
   - **Ruff** (unused imports, etc.): targeted edits.
   - **Logic errors**: read the failing file, diagnose, fix.
5. Sync changed files to `/tmp/fresh_portfolio`, commit, rebase, push (see Git Push Workflow above).
6. Loop back to step 1 to watch the new run.
7. Stop when `conclusion == "success"` and notify me.
