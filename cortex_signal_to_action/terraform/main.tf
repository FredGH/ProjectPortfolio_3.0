# All four databases and their schemas.
# Import existing objects created by bootstrap scripts:
#   terraform import 'module.databases.snowflake_database.this["dev"]'    CSTA_MARKETING_DEV
#   terraform import 'module.databases.snowflake_database.this["uat"]'    CSTA_MARKETING_UAT
#   terraform import 'module.databases.snowflake_database.this["prod"]'   CSTA_MARKETING_PROD
#   terraform import 'module.databases.snowflake_database.this["shared"]' CSTA_MARKETING_SHARED
module "databases" {
  source = "./modules/databases"
}

# Three compute warehouses (XS/S/M per env).
module "warehouses" {
  source = "./modules/warehouses"

  depends_on = [module.databases]
}

# Two-layer RBAC: access roles + functional roles + all privilege grants.
module "rbac" {
  source = "./modules/rbac"

  depends_on = [module.databases, module.warehouses]
}

# Internal dbt artifact stage + profiles.yml secrets (one per env).
module "stages" {
  source = "./modules/stages"

  depends_on = [module.databases, module.rbac]
}
