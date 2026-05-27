output "cluster_id" {
  value = aws_ecs_cluster.main.id
}

output "cluster_arn" {
  value = aws_ecs_cluster.main.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.ecs.name
}
