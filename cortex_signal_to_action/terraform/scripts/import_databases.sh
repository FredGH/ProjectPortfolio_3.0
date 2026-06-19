#!/usr/bin/env bash
# Import the 4 bootstrapped databases and 14 schemas into Terraform state.
# Must be run from the terraform/ directory with the dev backend initialised:
#   terraform init -backend-config=environments/dev.backend
#   bash scripts/import_databases.sh
set -euo pipefail

TFVARS="environments/dev.tfvars"

# ── Databases (4) ─────────────────────────────────────────────────────────────
for key_name in "dev:CSTA_MARKETING_DEV" "uat:CSTA_MARKETING_UAT" "prod:CSTA_MARKETING_PROD" "shared:CSTA_MARKETING_SHARED"; do
  key="${key_name%%:*}"
  name="${key_name##*:}"
  echo "Importing database [\"${key}\"] → ${name}"
  terraform import -var-file="$TFVARS" \
    "module.databases.snowflake_database.this[\"${key}\"]" \
    "$name"
done

# ── Schemas (14) ──────────────────────────────────────────────────────────────
# Key format: <db_key>_<SCHEMA_NAME> → <DB_NAME>.<SCHEMA_NAME>
declare -a SCHEMA_IMPORTS=(
  "dev_BRONZE:CSTA_MARKETING_DEV.BRONZE"
  "dev_SILVER:CSTA_MARKETING_DEV.SILVER"
  "dev_GOLD:CSTA_MARKETING_DEV.GOLD"
  "dev_ORCHESTRATION:CSTA_MARKETING_DEV.ORCHESTRATION"
  "uat_BRONZE:CSTA_MARKETING_UAT.BRONZE"
  "uat_SILVER:CSTA_MARKETING_UAT.SILVER"
  "uat_GOLD:CSTA_MARKETING_UAT.GOLD"
  "uat_ORCHESTRATION:CSTA_MARKETING_UAT.ORCHESTRATION"
  "prod_BRONZE:CSTA_MARKETING_PROD.BRONZE"
  "prod_SILVER:CSTA_MARKETING_PROD.SILVER"
  "prod_GOLD:CSTA_MARKETING_PROD.GOLD"
  "prod_ORCHESTRATION:CSTA_MARKETING_PROD.ORCHESTRATION"
  "shared_OBSERVABILITY:CSTA_MARKETING_SHARED.OBSERVABILITY"
  "shared_ARTIFACTS:CSTA_MARKETING_SHARED.ARTIFACTS"
)
for entry in "${SCHEMA_IMPORTS[@]}"; do
  key="${entry%%:*}"
  id="${entry##*:}"
  echo "Importing schema [\"${key}\"] → ${id}"
  terraform import -var-file="$TFVARS" \
    "module.databases.snowflake_schema.this[\"${key}\"]" \
    "$id"
done

echo ""
echo "All 4 databases and 14 schemas imported."
