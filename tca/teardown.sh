#!/usr/bin/env bash
# teardown.sh — destroy all TCA AWS resources after the 2-day demo.
#
# Usage:
#   export AWS_ACCESS_KEY_ID=...
#   export AWS_SECRET_ACCESS_KEY=...
#   export AWS_ACCOUNT_ID=...        # 12-digit account ID
#   export TF_VAR_db_password=...    # same password used at deploy time
#   ./teardown.sh
#
# Or pass them inline:
#   AWS_ACCOUNT_ID=123456789012 TF_VAR_db_password=TcaDemo2024! ./teardown.sh

set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-west-1}"
CLUSTER="tca-prod"
TF_DIR="$(dirname "$0")/terraform/environments/prod"
ECR_REPOS=("tca-api" "tca-mock-server" "tca-airflow" "tca-angular")

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

# ── Preflight checks ────────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}TCA Platform — AWS Teardown${NC}"
echo "────────────────────────────────────────────"

if ! command -v aws &>/dev/null; then
  echo -e "${RED}✗ aws CLI not found. Install: brew install awscli${NC}"; exit 1
fi
if ! command -v terraform &>/dev/null && ! command -v /opt/homebrew/bin/terraform &>/dev/null; then
  echo -e "${RED}✗ terraform not found. Install: brew install hashicorp/tap/terraform${NC}"; exit 1
fi
TF_BIN="$(command -v /opt/homebrew/bin/terraform 2>/dev/null || command -v terraform)"

for VAR in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_ACCOUNT_ID TF_VAR_db_password; do
  if [ -z "${!VAR:-}" ]; then
    echo -e "${RED}✗ $VAR is not set.${NC}"; exit 1
  fi
done

echo "  Region:  $REGION"
echo "  Account: $AWS_ACCOUNT_ID"
echo "  Cluster: $CLUSTER"
echo ""
echo -e "${RED}This will permanently destroy all TCA resources including RDS data.${NC}"
read -r -p "Type 'destroy' to confirm: " CONFIRM
if [ "$CONFIRM" != "destroy" ]; then
  echo "Aborted."; exit 0
fi
echo ""

# ── Step 1: Scale ECS services to 0 ────────────────────────────────────────

echo "▶ Scaling ECS services to 0 …"
for SVC in api mock-server airflow-webserver airflow-scheduler; do
  SVC_NAME="${CLUSTER}-${SVC}"
  if aws ecs describe-services \
       --cluster "$CLUSTER" --services "$SVC_NAME" \
       --query 'services[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
    aws ecs update-service \
      --cluster "$CLUSTER" --service "$SVC_NAME" \
      --desired-count 0 \
      --output text --query 'service.serviceName'
    echo "  scaled $SVC_NAME → 0"
  else
    echo "  $SVC_NAME not found or already gone"
  fi
done

echo "  Waiting 30 s for tasks to drain …"
sleep 30

# ── Step 2: Empty ECR repositories (terraform can't delete non-empty repos) ─

echo ""
echo "▶ Emptying ECR repositories …"
for REPO in "${ECR_REPOS[@]}"; do
  IMAGES=$(aws ecr list-images \
    --repository-name "$REPO" \
    --query 'imageIds[*]' \
    --output json 2>/dev/null || echo "[]")
  if [ "$IMAGES" != "[]" ] && [ "$IMAGES" != "" ]; then
    aws ecr batch-delete-image \
      --repository-name "$REPO" \
      --image-ids "$IMAGES" \
      --output text --query 'imageIds[*].imageTag' 2>/dev/null | tr '\t' '\n' | \
      sed "s/^/  deleted $REPO:/" || true
    echo "  $REPO emptied"
  else
    echo "  $REPO already empty"
  fi
done

# ── Step 3: terraform destroy ───────────────────────────────────────────────

echo ""
echo "▶ Running terraform destroy …"

# Write tfvars so terraform has what it needs to identify the state
cat > "$TF_DIR/terraform.tfvars" <<EOF
aws_account_id = "$AWS_ACCOUNT_ID"
db_password    = "$TF_VAR_db_password"
image_tag      = "latest"
aws_region     = "$REGION"
EOF

export AWS_DEFAULT_REGION="$REGION"

cd "$TF_DIR"
"$TF_BIN" init -upgrade -no-color
"$TF_BIN" destroy -auto-approve -no-color

# ── Done ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}✓ All TCA resources destroyed. No further AWS charges will accrue.${NC}"
echo ""
echo "Tip: confirm in the AWS Console that the following are gone:"
echo "  • ECS cluster:     $CLUSTER"
echo "  • RDS instance:    tca-prod"
echo "  • NAT Gateway:     tca-prod-nat  (stops the \$0.048/hr clock)"
echo "  • CloudFront dist: check Distributions list"
