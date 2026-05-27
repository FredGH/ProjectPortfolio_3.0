output "service_name" {
  value = aws_ecs_service.mock_server.name
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.mock_server.arn
}
