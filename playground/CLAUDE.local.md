# CLAUDE.local.md

Personal overrides — gitignored, not shared with the team.

## Local Setup

- Python: 3.11
- Virtual env: `venv/`
- GitHub repo: `FredGH/ProjectPortfolio_3.0`

## Git Push Workflow

The local git object store is corrupted. **Never commit or push directly from the local repo.**
Always push via the clean clone at `/tmp/fresh_portfolio`:

```bash
LOCAL_DIR="/Users/fredericmarechal/Documents/GitHub/courses/data-eng-courses/ai-engineering-courses/ai-agentic-eng-course/projects/agents/projects/ProjectPortfolio_3.0/claude_project_template"
FRESH="/tmp/fresh_portfolio"

rsync -av \
  --exclude='venv/' --exclude='.venv/' --exclude='*.egg-info/' \
  --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='.coverage' --exclude='dist/' --exclude='.ruff_cache/' \
  "$LOCAL_DIR/" "$FRESH/claude_project_template/"

cd /tmp/fresh_portfolio
git add claude_project_template/
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
