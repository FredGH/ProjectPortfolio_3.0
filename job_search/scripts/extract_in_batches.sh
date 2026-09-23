#!/bin/bash
# Runs `extract-job-skills` in bounded batches, unloading the Ollama model
# and pausing between each, instead of one long unbroken run.
#
# Why this exists: an unbroken ~50-job batch grew Ollama's resident
# llama-server process from ollama ps's reported ~5.0 GB to 9.31 GB (it is
# never idle long enough for OLLAMA_KEEP_ALIVE's default 5-minute timeout to
# unload it) and froze the whole Mac solid, confirmed by macOS's own jetsam
# memory-pressure report naming llama-server the largest process in memory
# at the exact moment of the freeze — see README.md's "Ollama's memory
# footprint grows across a long run" section for the full incident and the
# numbers.
#
# Safe to interrupt (Ctrl-C, a crash, closing the terminal) and re-run: each
# job commits its own rows the moment it succeeds, with no outer transaction
# around a batch (core.skills.write_job_skills.write_job_skills) — nothing
# already extracted is redone, and a re-run picks up exactly where it left
# off.
#
# Assumes a native Ollama on the host (README.md's "Running Ollama natively"
# section) and is run from the job_search directory outside Docker, the
# proven-working path as of this writing (apps/pipeline's Docker image needs
# a rebuild to pick up #35's httpx pin before `docker compose run pipeline`
# works again for LLM-backed commands).
#
# Usage:
#   scripts/extract_in_batches.sh [BATCH_SIZE] -- [extra extract-job-skills args...]
#
# Example (the Data/AI Greenhouse scope used throughout Step 14):
#   scripts/extract_in_batches.sh 30 -- --source greenhouse \
#     --category data_engineer --category analytics_engineer \
#     --category data_scientist --category ai_ml_engineer

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

BATCH_SIZE="${1:-30}"
shift || true
if [ "${1:-}" = "--" ]; then
  shift
fi
EXTRACT_ARGS=("$@")

MODEL="${OLLAMA_MODEL:-llama3.1:8b}"
PAUSE_SECONDS="${PAUSE_SECONDS:-10}"
MAX_BATCHES="${MAX_BATCHES:-50}"

if [ ! -f venv/bin/activate ]; then
  echo "extract_in_batches: no venv/ here — run from the job_search directory" >&2
  exit 1
fi
# shellcheck disable=SC1091
source venv/bin/activate
set -a
# shellcheck disable=SC1091
. ./.env
set +a
export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"
export APP_DATABASE_URL="${APP_DATABASE_URL/@postgres:/@localhost:}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL_OVERRIDE:-http://localhost:11434}"

batch=1
while [ "$batch" -le "$MAX_BATCHES" ]; do
  if ! docker info >/dev/null 2>&1; then
    echo "extract_in_batches: Docker is down — stopping" >&2
    exit 1
  fi
  if ! curl -sf -m 5 "$OLLAMA_BASE_URL/api/version" >/dev/null; then
    echo "extract_in_batches: Ollama is not answering at $OLLAMA_BASE_URL — stopping" >&2
    exit 1
  fi

  echo "=== batch $batch (limit $BATCH_SIZE) ==="
  t0=$(date +%s)
  PYTHONPATH=packages/core:apps/pipeline python -u -m app.cli extract-job-skills \
    --limit "$BATCH_SIZE" "${EXTRACT_ARGS[@]}"
  exit_code=$?
  t1=$(date +%s)
  echo "batch $batch: exit=$exit_code wall=$((t1 - t0))s"
  if [ "$exit_code" -ne 0 ]; then
    echo "extract_in_batches: batch $batch failed (every attempted job failed) — stopping" >&2
    exit "$exit_code"
  fi

  # Free the model's memory before the next batch rather than letting a
  # long run keep it resident and growing.
  ollama stop "$MODEL" >/dev/null 2>&1
  sleep "$PAUSE_SECONDS"

  pending=$(PYTHONPATH=packages/core:apps/pipeline python -c "
from core.db.session import build_engine
from core.settings import get_settings
from core.skills.write_job_skills import count_pending_jobs
import argparse, sys

# Re-parse just the --source/--category flags this run used, to count the
# same scope (a plain int compare against extract-job-skills' own printed
# count would also work, but re-querying is exact even if that line's
# wording ever changes).
p = argparse.ArgumentParser()
p.add_argument('--source', action='append', default=None)
p.add_argument('--category', action='append', default=None)
p.add_argument('--limit', type=int, default=None)
args, _ = p.parse_known_args(sys.argv[1:])
e = build_engine(get_settings().database_url)
print(count_pending_jobs(e, sources=args.source, categories=args.category))
" "${EXTRACT_ARGS[@]}" 2>/dev/null | tail -1)

  echo "batch $batch: $pending job(s) still pending in scope"
  if [ -z "$pending" ] || [ "$pending" -eq 0 ]; then
    echo "extract_in_batches: done — nothing left pending"
    break
  fi
  batch=$((batch + 1))
done
