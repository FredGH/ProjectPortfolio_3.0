output "dns_name" {
  value = aws_lb.main.dns_name
}

output "arn" {
  value = aws_lb.main.arn
}

output "api_target_group_arn" {
  value = aws_lb_target_group.api.arn
}

output "mock_target_group_arn" {
  value = aws_lb_target_group.mock.arn
}

output "airflow_target_group_arn" {
  value = aws_lb_target_group.airflow.arn
}
