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

### `brew services restart`/`start` always regenerates its own config — don't use them for this

Homebrew's `ollama` formula ships its service definition with
`OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` baked in. Editing
`~/Library/LaunchAgents/sh.brew.ollama.plist` directly looks like it works —
until the next `brew services restart` or `start`, which **regenerates that
file from the formula's own definition every single time**, silently
reverting the edit. Editing the copy of the plist under the Cellar
(`$(brew --cellar ollama)/*/sh.brew.ollama.plist`) does **not** help either —
`brew services` was confirmed (twice, on two different days) to ignore it and
regenerate from the formula regardless.

The only durable fix is to bypass `brew services` entirely: edit the
LaunchAgent, then load it directly with `launchctl`, and **never run
`brew services restart|start ollama` again afterward** (or redo this):

```bash
/usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables" ~/Library/LaunchAgents/sh.brew.ollama.plist
plutil -lint ~/Library/LaunchAgents/sh.brew.ollama.plist   # sanity-check the edit
launchctl bootout gui/$(id -u)/sh.brew.ollama
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/sh.brew.ollama.plist
launchctl kickstart -k gui/$(id -u)/sh.brew.ollama
# confirm: no OLLAMA_FLASH_ATTENTION / OLLAMA_KV_CACHE_TYPE in either of these
launchctl print gui/$(id -u)/sh.brew.ollama | grep -A5 'environment ='
ps eww -p "$(pgrep -f 'ollama serve' | head -1)" | tr ' ' '\n' | grep OLLAMA_
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

**Update:** this was implemented. Applying the penalty to every call was tried
first and rejected — it measurably hurt quality on chunks that never looped
(fewer skills found, and sometimes invalid JSON from inline comments the
penalty itself induced). What shipped instead retries only a chunk whose
first reply was truncated, once, with the penalty — a chunk that succeeds
normally is never touched by it. See `core.skills.jd_extract.REPEAT_PENALTY`.

### Ollama's memory footprint grows across a long run — it once froze the whole Mac

A batch of ~50 back-to-back extraction jobs (~45 minutes, no gaps — Ollama's
`OLLAMA_KEEP_ALIVE` default of 5 minutes never kicks in because the model is
never idle) grew the resident `llama-server` process from `ollama ps`'s
reported ~5.0 GB up to **9.31 GB**, confirmed by macOS's own memory-pressure
("jetsam") report at the moment of a full machine freeze that needed a hard
restart:

```
"largestProcess": "llama-server"
llama-server:                              9,310 MB
com.apple.Virtualization.VirtualMachine:   5,832 MB   (Docker Desktop's VM)
everything else combined:                 ~2,500 MB
                                           ---------
total:                                    ~15.1 GB of 16 GB, 176 MB free
```

(Recover this kind of evidence yourself with `ls -la
/Library/Logs/DiagnosticReports/JetsamEvent-*.ips` around the crash time, then
`python3 -c "import json; ..."` to parse it — it's readable JSON despite the
`.ips` extension, one header line then the report.)

**Mitigation:** don't run one large unbroken batch. `scripts/extract_in_batches.sh`
wraps `extract-job-skills` in bounded batches (30 by default), unloading the
model (`ollama stop`) and pausing between each — this is not a manual step to
remember, it is the committed, standard way to run a big extraction:

```bash
scripts/extract_in_batches.sh 30 -- --source greenhouse \
    --category data_engineer --category analytics_engineer \
    --category data_scientist --category ai_ml_engineer
```

It re-checks Docker and Ollama are up before every batch, and stops on the
first batch where every job failed rather than grinding on uselessly. The
batch-writer design already makes the whole thing safe to interrupt — each job
commits its rows the moment it succeeds, with no outer transaction around the
batch (`core.skills.write_job_skills.write_job_skills`; see "Scoping
extraction" in `docs/esco.md`), so nothing already extracted is at risk —
only the in-flight job when memory runs out.

**A second, independent contributor:** Docker Desktop's own VM was already
using 5.83 GB of its 7.9 GB allocation (`--memoryMiB 8092` on its
`com.docker.virtualization` process — found via `ps aux`, since none of
Docker Desktop's plain config files under `~/Library/Group Containers/
group.com.docker/` or `~/Library/Application Support/Docker Desktop/` hold the
live value; `marlin.dat` in the first directory looks promising but is a
telemetry log, not settings). Lowering it is a manual step — Docker Desktop's
newer versions keep VM resource settings in internal app state, not a
document one can safely edit from outside: **Docker Desktop → Settings →
Resources → Memory**, lower it (e.g. to 3–4 GB; this project's containers are
Postgres/FastAPI/Streamlit, which don't need much), **Apply & Restart**.

### Running a batch from the UI instead of the shell script

The **Skill Extraction Runner** page (`apps/ui/app/pages/7_Skill_Extraction_Runner.py`,
`http://localhost:8501/Skill_Extraction_Runner`) is the primary way to run a
scoped extraction batch, and the only way that's safe when Ollama runs in
Docker: `scripts/extract_in_batches.sh` (above) needs a native `venv/` and the
`ollama` CLI on the host, neither of which exist inside the `pipeline`
container, so it offers no protection at all when Ollama is Docker-hosted.
The script is unchanged and still works for a native-Ollama, CLI-only
workflow — this isn't a replacement, it's the option for everyone else.

The page triggers a background run via the API (`POST
/skills/extraction-runs`, see `core.skills.extraction_run.run_loop`), which
repeats the same bounded-sub-batch pattern — 30 jobs, then unload, then a 10s
pause — but over Ollama's own HTTP API (`POST /api/generate` with
`{"model": ..., "keep_alive": 0}`, no `prompt`) instead of the `ollama` CLI.
That's the whole reason it works identically for native or Docker-hosted
Ollama: it's a plain HTTP call to whatever `OLLAMA_BASE_URL` resolves to,
with no dependence on a CLI binary or a docker socket. Batch size (30) and
pause (10s) are fixed, not configurable from the UI — the exact values this
section already established as safe.

**Progress and Stop are per job.** After every job the run commits its counts
and bumps `updated_at`, so the page's bar moves as jobs finish, and a Stop is
noticed before the next job starts — measured live: Stop halted a native run
**37 s** after it was pressed (it finishes the job in flight first). The 30-job
sub-batch only decides when Ollama is unloaded, not how fast the page reacts.

**Ending a run.** A job that fails is retried once, in the next pass. If that
pass makes no progress: a run that had never extracted anything is marked
**failed** (Ollama or the model is broken for this scope — looping would burn
LLM time forever); a run that had extracted jobs is **completed**, and the
stubborn leftovers simply stay pending for the next run (each counted once in
`failed_count`).

**Skills are mapped automatically when a run completes.** Extraction only
writes raw strings; until they are mapped to ESCO they can't reach the review
list or the job–skill bridge. So a completed run maps its new strings itself
(what `map-skills` does — `core.skills.post_run_mapping`, in-process, as the
app DB role, embedding against the same Ollama location the run used) and shows
the outcome on the page and in `silver.skill_extraction_run.mapping_summary`,
e.g. *"Mapped 4 new skill string(s) to ESCO; 25 need review."* A stopped or
failed run skips it (so Stop stays instant, and a broken Ollama isn't asked to
embed); the next completed run maps everything still unmapped. A mapping error
never turns a completed run into a failed one — it is recorded in the summary.

**The dbt bridge is *not* automatic.** `silver__skill` and
`silver__bridge_job_skill` are dbt models, and dbt cannot run inside the API
image (dbt-core needs protobuf ≥ 6 while Streamlit needs < 6 — see
`requirements-dbt.txt`). The page shows the command after each completed run;
from `job_search/`, run it in the one-shot `dbt` container (its own image, so
no host venv is needed):

```bash
docker compose run --rm dbt run --select silver__skill silver__bridge_job_skill
```

(`docker compose run --rm dbt debug` checks the connection. The host-venv route
still works: `python3.11 -m venv venv-dbt && venv-dbt/bin/pip install -r
requirements-dbt.txt`, then `cd dbt && ../venv-dbt/bin/dbt run --select ...`.)
The API itself cannot start that container — that would need the Docker socket
mounted into it — so the refresh is one command after a run rather than
automatic.

A run's status is persisted in `silver.skill_extraction_run`, so it survives
an API restart rather than silently vanishing. **Known limitation:** if the
API process dies or restarts while a run is `running`, the row is left
`running` — **Stop only sets a flag for the run's own loop to notice, and if
that loop is gone there is nothing left to act on it.** Because a live run now
updates `updated_at` after every job, the page flags a run as stalled after
**30 minutes** of silence (it used to be 2 hours, when only whole batches
reported), and gives the one-line manual fix — only use it when the process is
verifiably gone, never on a run that is still alive (that would let a second
run start alongside it):

```sql
UPDATE silver.skill_extraction_run SET status = 'cancelled', finished_at = now(), updated_at = now() WHERE run_id = '<id>';
```

Not built: clearing an orphaned run automatically (from the API on startup, or
a "clear stalled run" button that checks the heartbeat server-side).

See `docs/superpowers/specs/2026-09-23-skill-extraction-batch-runner-design.md`
for the full design.
