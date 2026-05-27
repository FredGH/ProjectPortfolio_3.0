resource "aws_ecs_task_definition" "airflow_webserver" {
  family                   = "${var.name_prefix}-airflow-webserver"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name    = "tca-airflow-webserver"
      image   = var.image_url
      command = ["webserver"]
      portMappings = [
        { containerPort = 8080, protocol = "tcp" }
      ]
      environment = [
        { name = "AIRFLOW__CORE__EXECUTOR", value = "LocalExecutor" },
        { name = "AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION", value = "true" },
        { name = "AIRFLOW__CORE__LOAD_EXAMPLES", value = "false" },
      ]
      secrets = [
        { name = "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", valueFrom = var.airflow_db_secret_arn },
        { name = "AIRFLOW__WEBSERVER__SECRET_KEY", valueFrom = var.airflow_secret_key_arn },
        { name = "DATABASE_URL", valueFrom = var.db_secret_arn },
        { name = "REDIS_URL", valueFrom = var.redis_url_secret_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "airflow-web"
        }
      }
    }
  ])

  tags = merge(var.tags, { Name = "${var.name_prefix}-airflow-webserver" })
}

resource "aws_ecs_service" "airflow_webserver" {
  name                   = "${var.name_prefix}-airflow-webserver"
  cluster                = var.cluster_arn
  task_definition        = aws_ecs_task_definition.airflow_webserver.arn
  desired_count          = var.desired_count
  launch_type            = "FARGATE"
  enable_execute_command = true

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.sg_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "tca-airflow-webserver"
    container_port   = 8080
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-airflow-webserver" })
}
