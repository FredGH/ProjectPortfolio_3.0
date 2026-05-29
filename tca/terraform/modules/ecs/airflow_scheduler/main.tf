resource "aws_ecs_task_definition" "airflow_scheduler" {
  family                   = "${var.name_prefix}-airflow-scheduler"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name    = "tca-airflow-scheduler"
      image   = var.image_url
      command = ["scheduler"]
      environment = [
        { name = "AIRFLOW__CORE__EXECUTOR",                        value = "LocalExecutor" },
        { name = "AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION",     value = "true" },
        { name = "AIRFLOW__CORE__LOAD_EXAMPLES",                   value = "false" },
        # StatsD → CloudWatch agent sidecar (shares localhost network namespace)
        { name = "AIRFLOW__METRICS__STATSD_ON",                    value = "True" },
        { name = "AIRFLOW__METRICS__STATSD_HOST",                  value = "localhost" },
        { name = "AIRFLOW__METRICS__STATSD_PORT",                  value = "8125" },
        { name = "AIRFLOW__METRICS__STATSD_PREFIX",                value = "airflow" },
        { name = "AIRFLOW__LOGGING__REMOTE_LOGGING",              value = "True" },
        { name = "AIRFLOW__LOGGING__REMOTE_BASE_LOG_FOLDER",      value = "s3://${var.name_prefix}-airflow-logs/logs" },
        { name = "AIRFLOW__LOGGING__ENCRYPT_S3_LOGS",             value = "False" },
      ]
      secrets = [
        { name = "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", valueFrom = var.airflow_db_secret_arn },
        { name = "AIRFLOW__WEBSERVER__SECRET_KEY",      valueFrom = var.airflow_secret_key_arn },
        { name = "DATABASE_URL",                        valueFrom = var.db_secret_arn },
        { name = "REDIS_URL",                           valueFrom = var.redis_url_secret_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "airflow-scheduler"
        }
      }
    },
    {
      # CloudWatch agent receives StatsD on udp/:8125 and forwards to CloudWatch
      # Runs as a non-essential sidecar so a restart does not kill the scheduler
      name      = "cwagent"
      image     = "amazon/cloudwatch-agent:latest"
      essential = false
      environment = [
        {
          name = "CW_CONFIG_CONTENT"
          value = jsonencode({
            metrics = {
              namespace = "TCA/Airflow"
              metrics_collected = {
                statsd = {
                  service_address                = ":8125"
                  metrics_collection_interval    = 30
                  metrics_aggregation_interval   = 300
                }
              }
            }
          })
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "cwagent"
        }
      }
    }
  ])

  tags = merge(var.tags, { Name = "${var.name_prefix}-airflow-scheduler" })
}

resource "aws_ecs_service" "airflow_scheduler" {
  name                   = "${var.name_prefix}-airflow-scheduler"
  cluster                = var.cluster_arn
  task_definition        = aws_ecs_task_definition.airflow_scheduler.arn
  desired_count          = var.desired_count
  launch_type            = "FARGATE"
  enable_execute_command = true

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.sg_id]
    assign_public_ip = false
  }

  # No load_balancer block — scheduler has no HTTP interface

  tags = merge(var.tags, { Name = "${var.name_prefix}-airflow-scheduler" })
}
