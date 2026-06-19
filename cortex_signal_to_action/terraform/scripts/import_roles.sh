#!/usr/bin/env bash
# Import all 59 RBAC account roles into Terraform state.
# Must be run from the terraform/ directory with the dev backend initialised:
#   terraform init -backend-config=environments/dev.backend
#   bash scripts/import_roles.sh
set -euo pipefail

TFVARS="environments/dev.tfvars"

import_role() {
  local resource_type="$1"
  local role_name="$2"
  echo "Importing ${resource_type}[\"${role_name}\"]"
  terraform import -var-file="$TFVARS" \
    "module.rbac.snowflake_account_role.${resource_type}[\"${role_name}\"]" \
    "$role_name"
}

# ── Layer 1a: DB-level access roles (8) ──────────────────────────────────────
for db in CSTA_MARKETING_DEV CSTA_MARKETING_UAT CSTA_MARKETING_PROD CSTA_MARKETING_SHARED; do
  for tier in DB_READ DB_MODIFY; do
    import_role "db_access_roles" "${db}_${tier}"
  done
done

# ── Layer 1b: Schema-level access roles (42) ─────────────────────────────────
declare -a SCHEMAS=(
  "CSTA_MARKETING_DEV_BRONZE"
  "CSTA_MARKETING_DEV_SILVER"
  "CSTA_MARKETING_DEV_GOLD"
  "CSTA_MARKETING_DEV_ORCHESTRATION"
  "CSTA_MARKETING_UAT_BRONZE"
  "CSTA_MARKETING_UAT_SILVER"
  "CSTA_MARKETING_UAT_GOLD"
  "CSTA_MARKETING_UAT_ORCHESTRATION"
  "CSTA_MARKETING_PROD_BRONZE"
  "CSTA_MARKETING_PROD_SILVER"
  "CSTA_MARKETING_PROD_GOLD"
  "CSTA_MARKETING_PROD_ORCHESTRATION"
  "CSTA_MARKETING_SHARED_OBSERVABILITY"
  "CSTA_MARKETING_SHARED_ARTIFACTS"
)
for schema_key in "${SCHEMAS[@]}"; do
  for tier in READ READ_WRITE READ_WRITE_CREATE; do
    import_role "schema_access_roles" "${schema_key}_${tier}"
  done
done

# ── Layer 2: Functional roles (9) ────────────────────────────────────────────
for role in \
  CSTA_DBT_DEV_ROLE \
  CSTA_DBT_UAT_ROLE \
  CSTA_DBT_PROD_ROLE \
  CSTA_CORTEX_ROLE \
  CSTA_OBSERVER_ROLE \
  CSTA_STREAMLIT_ROLE \
  CSTA_ANALYST_ROLE \
  CSTA_DEV_ROLE \
  CSTA_UAT_DEV_ROLE; do
  import_role "functional_roles" "$role"
done

echo ""
echo "All 59 roles imported. Run: terraform plan -var-file=environments/dev.tfvars"
