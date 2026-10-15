#!/usr/bin/env bash
# Look at (and use) the Company Intel page without the full Docker stack.
# Postgres and, for Research, the Crawl4AI crawler run in Docker; a slim API and Streamlit run on this Mac with the
# project's Python environment (venv/). Ctrl+C stops the API and the UI.
#   ./scripts/demo_company_intel_ui.sh
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d postgres >/dev/null 2>&1
set -a; . ./.env; set +a
# .env uses the Docker host name "postgres"; from the Mac it is "localhost".
export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"
export APP_DATABASE_URL="${APP_DATABASE_URL/@postgres:/@localhost:}"
export API_BASE_URL="http://localhost:8000"
export PYTHONPATH="$PWD/packages/core:$PWD/apps/api:$PWD/scripts"

# Research needs the crawler. Start it when its image is already pulled and a
# token is set (see README); otherwise the page still works for viewing.
export CRAWL4AI_URL="http://localhost:11235"
if [ -n "${CRAWL4AI_API_TOKEN:-}" ] \
  && docker image inspect unclecode/crawl4ai:0.9.4 >/dev/null 2>&1; then
  docker compose --profile research up -d crawl4ai >/dev/null 2>&1
else
  echo "(crawler not started: Research will not work, viewing does)"
fi

for _ in $(seq 1 30); do
  docker compose exec -T postgres pg_isready -q 2>/dev/null && break
  sleep 2
done

venv/bin/uvicorn company_intel_demo_api:app --host 127.0.0.1 --port 8000 \
  --log-level warning &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT

echo
echo "Company Intel page:  http://localhost:8501/Company_Intel"
echo "(pick a job in the list; Ctrl+C here to stop)"
echo
venv/bin/streamlit run apps/ui/app/Home.py --server.address 127.0.0.1 \
  --server.port 8501 --server.headless true --browser.gatherUsageStats false
