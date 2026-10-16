#!/usr/bin/env bash
# Look at (and use) the Interview Prep page without the full Docker stack.
# Postgres runs in Docker; a slim API and Streamlit run on this Mac with the
# project's Python environment (venv/). Generating questions or drafting an answer calls Claude
# (the page asks you to confirm the cost first). Ctrl+C stops both.
#   ./scripts/demo_interview_ui.sh
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d postgres >/dev/null 2>&1
set -a; . ./.env; set +a
# .env uses the Docker host name "postgres"; from the Mac it is "localhost".
export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"
export APP_DATABASE_URL="${APP_DATABASE_URL/@postgres:/@localhost:}"
export API_BASE_URL="http://localhost:8000"
export PYTHONPATH="$PWD/packages/core:$PWD/apps/api:$PWD/scripts"

for _ in $(seq 1 30); do
  docker compose exec -T postgres pg_isready -q 2>/dev/null && break
  sleep 2
done

venv/bin/uvicorn interview_demo_api:app --host 127.0.0.1 --port 8000 \
  --log-level warning &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT

echo
echo "Interview Prep page:  http://localhost:8501/Interview_Prep"
echo "(Bank tab: generate a role family first. Ctrl+C here to stop)"
echo
venv/bin/streamlit run apps/ui/app/Home.py --server.address 127.0.0.1 \
  --server.port 8501 --server.headless true --browser.gatherUsageStats false
