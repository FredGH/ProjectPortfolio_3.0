# Job Search Platform

A job search pipeline: ingest postings from several sources, deduplicate them,
enrich and categorise them, normalise skills against ESCO, and (later phases)
score them against a CV. See `PLAN.md` for the step-by-step build plan,
`DECISIONS.md` for standing architectural decisions and their rationale, and
`docs/esco.md` for the skill-normalisation step in detail.

## Quick start

```bash
cp .env.example .env   # fill in POSTGRES_PASSWORD, APP_DB_PASSWORD, etc.
docker compose up -d postgres api ui
alembic -c db/alembic.ini upgrade head
```

The `api` (FastAPI) and `ui` (Streamlit) services come up on `localhost:8000`
and `localhost:8501`. Pipeline commands run via
`docker compose run --rm pipeline <command> [args]` — see `PLAN.md` and
`docs/esco.md` for the commands themselves.

## Local LLM (Ollama)

Several steps (skill extraction, CV extraction, categorisation's local track —
`DECISIONS.md` §1) call a local model through Ollama. `docker-compose.yml`
defines an `ollama` service for this, but **it is CPU-only inside Docker on
macOS** — Docker Desktop cannot pass the Mac's GPU into a container. On a
CPU-only container, `llama3.1:8b` took **~120 s per job description** in
testing (one real 8k-character job: 119.4 s, 10 skills).

### Running Ollama natively instead (Apple Silicon: ~3x faster)

Installing Ollama directly on the Mac lets it use the GPU (Metal) via
`llama.cpp`, instead of running through Docker's CPU-only path. Measured on
the same job as above: **34–40 s** (vs 119.4 s in Docker), and the
`skill_extraction` golden-set eval scored 0.963 either way — no quality loss
observed.

```bash
brew install ollama
brew services start ollama
ollama pull llama3.1:8b
ollama pull nomic-embed-text        # whatever EMBEDDING_MODEL names
ollama ps                           # confirm PROCESSOR shows 100% GPU
```

A command run **outside** Docker points at it directly
(`OLLAMA_BASE_URL=http://localhost:11434`); a command run **inside** a
container points at it via `OLLAMA_BASE_URL=http://host.docker.internal:11434`
(`docker compose run -e OLLAMA_BASE_URL=http://host.docker.internal:11434 …`) —
the plain hostname `ollama` that `.env` normally resolves only exists inside
the compose network. Stop the Docker `ollama` service first
(`docker compose stop ollama`) — both would otherwise fight over port 11434 on
the host.

### Two Homebrews, one silent trap

A Mac that was ever set up with an Intel Homebrew (`/usr/local/bin/brew`) may
still have it ahead of the Apple Silicon one (`/opt/homebrew/bin/brew`) on
`PATH`. `brew install ollama` through the wrong one installs an **x86_64
build that runs under Rosetta, CPU-only** — same speed problem as Docker, with
no error or warning. Check before relying on any timing:

```bash
file "$(brew --prefix ollama)/libexec/lib/ollama/llama-server"
# must say "Mach-O 64-bit executable arm64", not "x86_64"
ollama ps   # PROCESSOR column must say "100% GPU", not "100% CPU"
```

If it's the wrong build: `brew services stop ollama && brew uninstall ollama`,
then repeat the install with the *Apple Silicon* brew explicitly:
`/opt/homebrew/bin/brew install ollama`. The downloaded models in `~/.ollama`
are untouched by an uninstall/reinstall.

### `brew services restart` regenerates its own config — edits to the LaunchAgent don't stick

Homebrew's `ollama` formula ships its service definition with
`OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` baked in. Editing
`~/Library/LaunchAgents/sh.brew.ollama.plist` directly looks like it works,
but **`brew services restart`/`start` overwrites that file from Homebrew's own
template** in the Cellar on every restart, silently reverting the edit. To
change it durably, edit the template itself, then reload:

```bash
plutil -lint "$(brew --cellar ollama)"/*/sh.brew.ollama.plist   # find it, sanity-check it
/usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables" "$(brew --cellar ollama)"/*/sh.brew.ollama.plist
brew services restart ollama
# if launchd still shows the old env (`launchctl print gui/$(id -u)/sh.brew.ollama`),
# force a reload instead of trusting the restart:
launchctl bootout gui/$(id -u)/sh.brew.ollama
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/sh.brew.ollama.plist
launchctl kickstart -k gui/$(id -u)/sh.brew.ollama
```

### A known decoding failure: indefinite repetition at `temperature=0`

Ollama's default repetition penalty only looks back 64 tokens
(`repeat_last_n`). `llama3.1:8b`, run deterministically (`temperature=0`, as
every call here is — see `docs/esco.md`), can settle into repeating a block of
output longer than that window **forever**, never emitting its stop token. One
real job triggered this: the model produced a correct skill list, then
repeated the same ~20 skills verbatim until cut off — over **9,300 tokens**,
10+ minutes, before being killed.

`core.skills.jd_extract.MAX_OUTPUT_TOKENS` (2,048) bounds the damage: a
looping reply is discarded and the job is retried next run, rather than
stalling forever. That is a safety net, not a fix — the same job still burns
~2-5 minutes hitting the cap before failing, on every retry.

**The actual fix, verified but not yet implemented in code**: an explicit
`repeat_penalty` with a `repeat_last_n` wide enough to cover the repeating
block stops the loop outright. Tested directly against the two real chunks
that looped:

| Setting | Result |
|---|---|
| Ollama defaults (as shipped) | hit the 2,048-token cap after 250-300 s, discarded |
| `repeat_penalty=1.3` | fast (7-22 s), but drifted out of JSON on one chunk — too aggressive |
| `repeat_penalty=1.15`, `repeat_last_n=256` | fast (9-28 s) and parsed cleanly on all 4 chunks tested |

If this keeps recurring at scale, the fix is to thread `repeat_penalty` /
`repeat_last_n` through the same optional-parameter path `max_tokens` already
uses (`core.llm.types.LLMAdapter.complete` → `core.llm.adapters.ollama.OllamaAdapter`
→ `core.llm.gateway.complete`), scoped to `skill_extraction` only, then
re-run the golden-set eval before trusting it broadly.
