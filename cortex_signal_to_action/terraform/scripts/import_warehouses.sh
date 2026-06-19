#!/usr/bin/env bash
# Import the 3 bootstrapped warehouses into Terraform state.
# Must be run from the terraform/ directory with the dev backend initialised:
#   terraform init -backend-config=environments/dev.backend
#   bash scripts/import_warehouses.sh
set -euo pipefail

TFVARS="environments/dev.tfvars"

echo "Importing module.warehouses.snowflake_warehouse.this[\"dev\"]"
terraform import -var-file="$TFVARS" 'module.warehouses.snowflake_warehouse.this["dev"]' CSTA_DBT_DEV_WH

echo "Importing module.warehouses.snowflake_warehouse.this[\"uat\"]"
terraform import -var-file="$TFVARS" 'module.warehouses.snowflake_warehouse.this["uat"]' CSTA_DBT_UAT_WH

echo "Importing module.warehouses.snowflake_warehouse.this[\"prod\"]"
terraform import -var-file="$TFVARS" 'module.warehouses.snowflake_warehouse.this["prod"]' CSTA_DBT_PROD_WH

echo ""
echo "All 3 warehouses imported."
