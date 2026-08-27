# Backup Local Config Skill

Syncs every `*.local.*` file under `ProjectPortfolio_3.0` into the `local-config-backup` repo, then commits and pushes — auto-invoked when a local-config backup is requested.

## Trigger Conditions

Invoked when the user requests:
- "Backup local config" / "back up the .local files"
- "Run backup-local-config"
- Syncing `CLAUDE.local.md` / `settings.local.json` files to their backup

## Background

`CLAUDE.local.md` and `.claude/settings.local.json` files are gitignored on purpose — they're personal, per-machine overrides and never belong in the shared repo. That means they have no backup of their own. `~/Documents/GitHub/local-config-backup` is a separate, private repo that exists solely to mirror these files (see `sync.sh` and its `.gitignore` override there, which re-includes `settings.local.json` despite the global gitignore rule that hides it everywhere else).

This is deliberately manual, not scheduled — an earlier attempt to automate it via `launchd` hit macOS's Full Disk Access restriction on background processes and was reverted in favor of running it on demand.

## Workflow

### Step 1 — Run the sync

```bash
bash /Users/fredericmarechal/Documents/GitHub/local-config-backup/sync.sh
```

Equivalent to the `backup-local-config` shell alias (`~/.zshrc`). This copies every `*.local.*` file under `ProjectPortfolio_3.0` into `~/Documents/GitHub/local-config-backup` (mirroring relative paths), commits if anything changed, and pushes to `origin/main`. If nothing changed, it exits cleanly with "No changes to back up."

### Step 2 — If the push is blocked

`git push` from inside a Claude Code session can be blocked by the permission classifier (pushing personal-config content to a repo reads as sensitive). If that happens, do not retry it repeatedly — report it and give the user the exact command to run themselves:

```bash
cd /Users/fredericmarechal/Documents/GitHub/local-config-backup
git push -u origin main
```

## Usage

```
/backup-local-config
```
