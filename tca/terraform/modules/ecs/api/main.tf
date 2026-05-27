resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name_prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name    = "tca-api"
      image   = var.image_url
      command = ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
      portMappings = [
        { containerPort = 8000, protocol = "tcp" }
      ]
      secrets = [
        { name = "DATABASE_URL", valueFrom = var.db_secret_arn },
        { name = "REDIS_URL", valueFrom = var.redis_url_secret_arn },
        { name = "JWT_PRIVATE_KEY", valueFrom = var.jwt_private_key_secret_arn },
        { name = "JWT_PUBLIC_KEY", valueFrom = var.jwt_public_key_secret_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
        }
      }
    }
  ])

  tags = merge(var.tags, { Name = "${var.name_prefix}-api" })
}

resource "aws_ecs_service" "api" {
  name            = "${var.name_prefix}-api"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.sg_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "tca-api"
    container_port   = 8000
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-api" })
}
