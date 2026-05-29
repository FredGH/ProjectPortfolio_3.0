locals {
  cluster_name = "${var.name_prefix}-cluster"

  # Key  = ECS service suffix (appended to name_prefix)
  # Value = human-readable label for widget titles
  services = {
    "api"               = "API"
    "airflow-webserver" = "Airflow Webserver"
    "airflow-scheduler" = "Airflow Scheduler"
    "mock-server"       = "Mock Server"
  }

  # Ordered list with column position for the 4-column dashboard layout
  service_list = [
    { key = "api",               label = "API",               log_prefix = "api",               col = 0 },
    { key = "airflow-webserver", label = "Airflow Webserver", log_prefix = "airflow-webserver", col = 6 },
    { key = "airflow-scheduler", label = "Airflow Scheduler", log_prefix = "airflow-scheduler", col = 12 },
    { key = "mock-server",       label = "Mock Server",       log_prefix = "mock-server",       col = 18 },
  ]
}

# ── SNS ────────────────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
  tags = merge(var.tags, { Name = "${var.name_prefix}-alarms" })
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# Allow AWS Budgets to publish to the SNS topic
resource "aws_sns_topic_policy" "allow_budgets" {
  arn = aws_sns_topic.alarms.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AWSBudgetsPublish"
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "SNS:Publish"
      Resource  = aws_sns_topic.alarms.arn
    }]
  })
}

# ── AWS Budgets ────────────────────────────────────────────────────────────────

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Notify at 80 % of actual spend
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alarms.arn]
  }

  # Notify when forecast exceeds 100 % of budget
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.alarms.arn]
  }
}

# ── ECS CPU alarms ─────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "ecs_cpu_high" {
  for_each = local.services

  alarm_name          = "${var.name_prefix}-${each.key}-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.cpu_alarm_threshold
  alarm_description   = "ECS ${each.value} CPU > ${var.cpu_alarm_threshold}%"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "pct"
    expression  = "m1/m2*100"
    label       = "CPU %"
    return_data = true
  }
  metric_query {
    id = "m1"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "CpuUtilized"
      period      = 60
      stat        = "Average"
      dimensions = {
        ServiceName = "${var.name_prefix}-${each.key}"
        ClusterName = local.cluster_name
      }
    }
  }
  metric_query {
    id = "m2"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "CpuReserved"
      period      = 60
      stat        = "Average"
      dimensions = {
        ServiceName = "${var.name_prefix}-${each.key}"
        ClusterName = local.cluster_name
      }
    }
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-${each.key}-cpu-high" })
}

# ── ECS Memory alarms ──────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "ecs_memory_high" {
  for_each = local.services

  alarm_name          = "${var.name_prefix}-${each.key}-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = var.memory_alarm_threshold
  alarm_description   = "ECS ${each.value} memory > ${var.memory_alarm_threshold}%"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "pct"
    expression  = "m1/m2*100"
    label       = "Memory %"
    return_data = true
  }
  metric_query {
    id = "m1"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "MemoryUtilized"
      period      = 60
      stat        = "Average"
      dimensions = {
        ServiceName = "${var.name_prefix}-${each.key}"
        ClusterName = local.cluster_name
      }
    }
  }
  metric_query {
    id = "m2"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "MemoryReserved"
      period      = 60
      stat        = "Average"
      dimensions = {
        ServiceName = "${var.name_prefix}-${each.key}"
        ClusterName = local.cluster_name
      }
    }
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-${each.key}-memory-high" })
}

# ── RDS alarms ─────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "${var.name_prefix}-rds-cpu-high"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = var.rds_instance_id }
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.cpu_alarm_threshold
  evaluation_periods  = 3
  period              = 60
  statistic           = "Average"
  alarm_description   = "RDS CPU > ${var.cpu_alarm_threshold}%"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = merge(var.tags, { Name = "${var.name_prefix}-rds-cpu-high" })
}

resource "aws_cloudwatch_metric_alarm" "rds_connections_high" {
  alarm_name          = "${var.name_prefix}-rds-connections-high"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = { DBInstanceIdentifier = var.rds_instance_id }
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.rds_connections_threshold
  evaluation_periods  = 3
  period              = 60
  statistic           = "Average"
  alarm_description   = "RDS connections > ${var.rds_connections_threshold}"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = merge(var.tags, { Name = "${var.name_prefix}-rds-connections-high" })
}

resource "aws_cloudwatch_metric_alarm" "rds_storage_low" {
  alarm_name          = "${var.name_prefix}-rds-storage-low"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = var.rds_instance_id }
  comparison_operator = "LessThanThreshold"
  threshold           = var.rds_free_storage_bytes
  evaluation_periods  = 1
  period              = 300
  statistic           = "Average"
  alarm_description   = "RDS free storage < 1 GB"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = merge(var.tags, { Name = "${var.name_prefix}-rds-storage-low" })
}

# ── CloudWatch Dashboard ───────────────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "tca" {
  dashboard_name = "${var.name_prefix}-observability"

  dashboard_body = jsonencode({
    widgets = concat(

      # ── ECS CPU section ──────────────────────────────────────────────────────
      [{
        type = "text", x = 0, y = 0, width = 24, height = 1
        properties = { markdown = "## ECS Services — CPU Utilisation" }
      }],

      [for svc in local.service_list : {
        type = "metric", x = svc.col, y = 1, width = 6, height = 6
        properties = {
          title  = "${svc.label} — CPU %"
          view   = "timeSeries"
          region = var.aws_region
          period = 60
          metrics = [
            [{ expression = "m1/m2*100", label = "CPU %", id = "e1", region = var.aws_region }],
            ["ECS/ContainerInsights", "CpuUtilized",
              "ServiceName", "${var.name_prefix}-${svc.key}",
              "ClusterName", local.cluster_name,
              { id = "m1", visible = false, region = var.aws_region }],
            ["ECS/ContainerInsights", "CpuReserved",
              "ServiceName", "${var.name_prefix}-${svc.key}",
              "ClusterName", local.cluster_name,
              { id = "m2", visible = false, region = var.aws_region }],
          ]
          yAxis       = { left = { min = 0, max = 100 } }
          annotations = { horizontal = [{ value = var.cpu_alarm_threshold, color = "#ff6961", label = "Alarm" }] }
        }
      }],

      # ── ECS Memory section ───────────────────────────────────────────────────
      [{
        type = "text", x = 0, y = 7, width = 24, height = 1
        properties = { markdown = "## ECS Services — Memory Utilisation" }
      }],

      [for svc in local.service_list : {
        type = "metric", x = svc.col, y = 8, width = 6, height = 6
        properties = {
          title  = "${svc.label} — Memory %"
          view   = "timeSeries"
          region = var.aws_region
          period = 60
          metrics = [
            [{ expression = "m1/m2*100", label = "Memory %", id = "e1", region = var.aws_region }],
            ["ECS/ContainerInsights", "MemoryUtilized",
              "ServiceName", "${var.name_prefix}-${svc.key}",
              "ClusterName", local.cluster_name,
              { id = "m1", visible = false, region = var.aws_region }],
            ["ECS/ContainerInsights", "MemoryReserved",
              "ServiceName", "${var.name_prefix}-${svc.key}",
              "ClusterName", local.cluster_name,
              { id = "m2", visible = false, region = var.aws_region }],
          ]
          yAxis       = { left = { min = 0, max = 100 } }
          annotations = { horizontal = [{ value = var.memory_alarm_threshold, color = "#ff6961", label = "Alarm" }] }
        }
      }],

      # ── Application Logs section ─────────────────────────────────────────────
      [{
        type = "text", x = 0, y = 14, width = 24, height = 1
        properties = { markdown = "## Application Logs" }
      }],

      [
        { type = "log", x = 0, y = 15, width = 12, height = 8
          properties = {
            title  = "API"
            query  = "SOURCE '${var.log_group_name}' | fields @timestamp, @message | filter @logStream like 'api/' | sort @timestamp desc | limit 100"
            region = var.aws_region
            view   = "table"
          }
        },
        { type = "log", x = 12, y = 15, width = 12, height = 8
          properties = {
            title  = "Airflow Webserver"
            query  = "SOURCE '${var.log_group_name}' | fields @timestamp, @message | filter @logStream like 'airflow-webserver/' | sort @timestamp desc | limit 100"
            region = var.aws_region
            view   = "table"
          }
        },
        { type = "log", x = 0, y = 23, width = 12, height = 8
          properties = {
            title  = "Airflow Scheduler"
            query  = "SOURCE '${var.log_group_name}' | fields @timestamp, @message | filter @logStream like 'airflow-scheduler/' | sort @timestamp desc | limit 100"
            region = var.aws_region
            view   = "table"
          }
        },
        { type = "log", x = 12, y = 23, width = 12, height = 8
          properties = {
            title  = "Mock Server"
            query  = "SOURCE '${var.log_group_name}' | fields @timestamp, @message | filter @logStream like 'mock-server/' | sort @timestamp desc | limit 100"
            region = var.aws_region
            view   = "table"
          }
        },
      ],

      # ── RDS section ──────────────────────────────────────────────────────────
      [{
        type = "text", x = 0, y = 31, width = 24, height = 1
        properties = { markdown = "## RDS Postgres" }
      }],

      [
        { type = "metric", x = 0, y = 32, width = 8, height = 6
          properties = {
            title   = "CPU %"
            view    = "timeSeries"
            region  = var.aws_region
            period  = 60
            metrics = [["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.rds_instance_id, { region = var.aws_region }]]
            yAxis   = { left = { min = 0, max = 100 } }
            annotations = { horizontal = [{ value = var.cpu_alarm_threshold, color = "#ff6961", label = "Alarm" }] }
          }
        },
        { type = "metric", x = 8, y = 32, width = 8, height = 6
          properties = {
            title   = "Connections"
            view    = "timeSeries"
            region  = var.aws_region
            period  = 60
            metrics = [["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", var.rds_instance_id, { region = var.aws_region }]]
            annotations = { horizontal = [{ value = var.rds_connections_threshold, color = "#ff6961", label = "Alarm" }] }
          }
        },
        { type = "metric", x = 16, y = 32, width = 8, height = 6
          properties = {
            title   = "Free Storage (bytes)"
            view    = "timeSeries"
            region  = var.aws_region
            period  = 300
            metrics = [["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", var.rds_instance_id, { region = var.aws_region }]]
            annotations = { horizontal = [{ value = var.rds_free_storage_bytes, color = "#ff6961", label = "Alarm (1 GB)" }] }
          }
        },
      ]

    )
  })
}
