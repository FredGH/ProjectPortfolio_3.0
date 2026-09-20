# CLAUDE.local.md

Personal overrides — gitignored, not shared with the team.

## Local Setup

- Python: 3.11
- Virtual env: `venv/`
- GitHub repo: `FredGH/ProjectPortfolio_3.0`

## Git Push Workflow

**2026-09-02 update:** Verified the local repo's object store directly —
`git fsck` shows no corruption (only harmless dangling objects from past
amends/rebases), and a live test (`git worktree add`, commit, push-shaped
write, cleanup) succeeded end to end. Commit and push directly from the
local repo; the `/tmp/fresh_portfolio` relay below is no longer needed for
that.

The relay is kept only as a documented fallback in case a future session
hits a real write failure (disk full, permissions, a genuinely corrupted
pack) — don't reach for it unless a direct `git commit`/`git push` in the
local repo actually errors:

```bash
PROJECT="$(basename "$PWD")"
LOCAL_DIR="$PWD"
FRESH="/tmp/fresh_portfolio"

rsync -av \
  --exclude='venv/' --exclude='.venv/' --exclude='*.egg-info/' \
  --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='.coverage' --exclude='dist/' --exclude='.ruff_cache/' \
  "$LOCAL_DIR/" "$FRESH/$PROJECT/"

cd /tmp/fresh_portfolio
git add "$PROJECT/"
git commit -m "..."
git fetch origin && git rebase origin/main && git push origin main
```

If `/tmp/fresh_portfolio` is missing or its own `.git` looks broken
(`fatal: not a git repository` despite a `.git/` directory being present is
what that looked like on 2026-09-02), just re-clone it:
```bash
rm -rf /tmp/fresh_portfolio
git clone https://github.com/FredGH/ProjectPortfolio_3.0.git /tmp/fresh_portfolio
```

## Personal Preferences

- Concise responses — no trailing summaries
- File references as clickable markdown links (`[file.py](path/to/file.py)`)
