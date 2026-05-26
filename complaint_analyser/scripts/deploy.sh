#!/usr/bin/env bash
# Deploy complaint_analyser to Oracle Cloud via SSH.
#
# Required environment variables (set in CI or locally):
#   DEPLOY_HOST      — Oracle VM public IP or hostname
#   DEPLOY_USER      — SSH user (default: ubuntu)
#   DEPLOY_SSH_KEY   — Path to private key file (or base64-encoded key in CI)
#
# Usage (local):
#   DEPLOY_HOST=1.2.3.4 DEPLOY_USER=ubuntu DEPLOY_SSH_KEY=~/.ssh/oracle_id_rsa \
#     bash scripts/deploy.sh
#
# The remote host must have Docker + Docker Compose V2 installed and the repo
# cloned to /opt/complaint_analyser. Run scripts/bootstrap_remote.sh once to
# set this up on a fresh Oracle VM.

set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST is required}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
DEPLOY_SSH_KEY="${DEPLOY_SSH_KEY:?DEPLOY_SSH_KEY is required}"
REMOTE_DIR="${REMOTE_DIR:-/opt/complaint_analyser}"

echo "==> Deploying to ${DEPLOY_USER}@${DEPLOY_HOST}:${REMOTE_DIR}"

ssh -i "${DEPLOY_SSH_KEY}" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=30 \
    "${DEPLOY_USER}@${DEPLOY_HOST}" \
    bash -s << 'REMOTE'
set -euo pipefail
cd /opt/complaint_analyser

echo "--- pulling latest code"
git pull origin main

echo "--- pulling updated images"
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull --quiet

echo "--- restarting services (zero-downtime for stateless services)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --wait \
    --remove-orphans \
    --timeout 120

echo "--- pruning unused images"
docker image prune -f --filter "until=24h"

echo "--- service status"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
REMOTE

echo "==> Deploy complete"
