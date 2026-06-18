locals {
  warehouses = {
    dev = {
      name           = "CSTA_DBT_DEV_WH"
      warehouse_size = "XSMALL"
      comment        = "Dev environment — feature branch dbt runs and ad-hoc analyst queries"
    }
    uat = {
      name           = "CSTA_DBT_UAT_WH"
      warehouse_size = "SMALL"
      comment        = "UAT environment — scheduled uat branch pipeline (cron 02:00 UTC)"
    }
    prod = {
      name           = "CSTA_DBT_PROD_WH"
      warehouse_size = "MEDIUM"
      comment        = "Prod environment — scheduled prod pipeline (cron 04:00 UTC) and TASK_COST_REPORT"
    }
  }
}

resource "snowflake_warehouse" "this" {
  for_each = local.warehouses

  name                = each.value.name
  warehouse_size      = each.value.warehouse_size
  auto_suspend        = 60
  auto_resume         = true
  initially_suspended = true
  comment             = each.value.comment
}
