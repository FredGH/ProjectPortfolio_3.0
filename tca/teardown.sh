#!/usr/bin/env bash
# teardown.sh — trigger the GitHub Actions teardown workflow for TCA.
#
# Prerequisites:
#   gh CLI installed and authenticated  (brew install gh && gh auth login)
#
# Usage:
#   ./teardown.sh
#
# AWS credentials are NOT required locally; the workflow uses GitHub Secrets.

set -euo pipefail

REPO="FredGH/ProjectPortfolio_3.0"
WORKFLOW="teardown-tca.yml"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

# ── Preflight checks ────────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}TCA Platform — AWS Teardown${NC}"
echo "────────────────────────────────────────────"

if ! command -v gh &>/dev/null; then
  echo -e "${RED}✗ gh CLI not found. Install: brew install gh${NC}"; exit 1
fi

if ! gh auth status &>/dev/null; then
  echo -e "${RED}✗ gh CLI not authenticated. Run: gh auth login${NC}"; exit 1
fi

echo "  Repo:     $REPO"
echo "  Workflow: $WORKFLOW"
echo ""
echo -e "${RED}This will permanently destroy all TCA resources including RDS data.${NC}"
read -r -p "Type 'destroy' to confirm: " CONFIRM
if [ "$CONFIRM" != "destroy" ]; then
  echo "Aborted."; exit 0
fi
echo ""

# ── Trigger workflow ────────────────────────────────────────────────────────

echo "▶ Triggering teardown workflow …"
gh workflow run "$WORKFLOW" \
  --repo "$REPO" \
  --ref main \
  -f confirm=destroy

# Give GitHub a moment to register the run
sleep 5

# ── Tail the run ────────────────────────────────────────────────────────────

echo "▶ Waiting for workflow run to appear …"
RUN_ID=""
for i in $(seq 1 12); do
  RUN_ID=$(gh run list \
    --repo "$REPO" \
    --workflow "$WORKFLOW" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId' 2>/dev/null || true)
  if [ -n "$RUN_ID" ] && [ "$RUN_ID" != "null" ]; then
    break
  fi
  sleep 5
done

if [ -z "$RUN_ID" ] || [ "$RUN_ID" = "null" ]; then
  echo -e "${RED}✗ Could not find the triggered run. Check:${NC}"
  echo "  gh run list --repo $REPO --workflow $WORKFLOW"
  exit 1
fi

echo "  Run ID: $RUN_ID"
echo "  View:   https://github.com/$REPO/actions/runs/$RUN_ID"
echo ""

gh run watch "$RUN_ID" --repo "$REPO" --exit-status

# ── Done ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}✓ Teardown workflow completed. No further AWS charges will accrue.${NC}"
echo ""
echo "Tip: confirm in the AWS Console that the following are gone:"
echo "  • ECS cluster:     tca-prod"
echo "  • RDS instance:    tca-prod"
echo "  • NAT Gateway:     tca-prod-nat  (stops the \$0.048/hr clock)"
echo "  • CloudFront dist: check Distributions list"
