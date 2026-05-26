#!/usr/bin/env bash
# One-time setup for a fresh Oracle Cloud Ubuntu 22.04 ARM VM.
#
# Run from your local machine:
#   DEPLOY_HOST=1.2.3.4 DEPLOY_USER=ubuntu DEPLOY_SSH_KEY=~/.ssh/oracle_id_rsa \
#     bash scripts/bootstrap_remote.sh
#
# After this script completes:
#   1. Copy your .env file to /opt/complaint_analyser/.env on the remote host.
#   2. Run: cloudflared tunnel login && cloudflared tunnel create complaint-analyser
#   3. Set CLOUDFLARE_TUNNEL_TOKEN in .env.
#   4. Run scripts/deploy.sh to start all services.

set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:?DEPLOY_HOST is required}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
DEPLOY_SSH_KEY="${DEPLOY_SSH_KEY:?DEPLOY_SSH_KEY is required}"
REPO_URL="${REPO_URL:-https://github.com/FredGH/ProjectPortfolio_3.0.git}"
REMOTE_DIR="/opt/complaint_analyser"

echo "==> Bootstrapping ${DEPLOY_USER}@${DEPLOY_HOST}"

ssh -i "${DEPLOY_SSH_KEY}" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=30 \
    "${DEPLOY_USER}@${DEPLOY_HOST}" \
    bash -s << REMOTE
set -euo pipefail

echo "--- installing Docker"
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker \$USER
fi

echo "--- installing cloudflared"
if ! command -v cloudflared &>/dev/null; then
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
        | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared focal main" \
        | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt-get update -qq && sudo apt-get install -y cloudflared
fi

echo "--- cloning repository"
sudo mkdir -p ${REMOTE_DIR}
sudo chown \$USER:\$USER ${REMOTE_DIR}
if [ ! -d "${REMOTE_DIR}/.git" ]; then
    git clone ${REPO_URL} /tmp/portfolio_clone
    cp -r /tmp/portfolio_clone/complaint_analyser/. ${REMOTE_DIR}/
    rm -rf /tmp/portfolio_clone
fi

echo "--- pulling Ollama models (background)"
docker run --rm -d \
    -v ollama_data:/root/.ollama \
    --name ollama_setup \
    ollama/ollama:latest || true
sleep 5
docker exec ollama_setup ollama pull llama3.1:8b &
docker exec ollama_setup ollama pull nomic-embed-text &
wait
docker stop ollama_setup 2>/dev/null || true

echo "--- bootstrap complete"
echo "Next: copy .env to ${REMOTE_DIR}/.env, then run scripts/deploy.sh"
REMOTE

echo "==> Bootstrap complete"
