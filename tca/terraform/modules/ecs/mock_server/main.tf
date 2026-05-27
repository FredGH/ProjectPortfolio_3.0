resource "aws_ecs_task_definition" "mock_server" {
  family                   = "${var.name_prefix}-mock-server"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([
    {
      name    = "tca-mock-server"
      image   = var.image_url
      command = ["uvicorn", "ingestion.mock.mock_server:app", "--host", "0.0.0.0", "--port", "8001"]
      portMappings = [
        { containerPort = 8001, protocol = "tcp" }
      ]
      secrets = [
        { name = "DATABASE_URL", valueFrom = var.db_secret_arn },
        { name = "REDIS_URL", valueFrom = var.redis_url_secret_arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "mock"
        }
      }
    }
  ])

  tags = merge(var.tags, { Name = "${var.name_prefix}-mock-server" })
}

resource "aws_ecs_service" "mock_server" {
  name            = "${var.name_prefix}-mock-server"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.mock_server.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.sg_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "tca-mock-server"
    container_port   = 8001
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-mock-server" })
}
