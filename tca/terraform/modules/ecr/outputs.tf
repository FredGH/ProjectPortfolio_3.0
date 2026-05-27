output "repository_urls" {
  description = "Map of image name → ECR repository URL"
  value       = { for k, v in aws_ecr_repository.repos : k => v.repository_url }
}

output "api_repository_url" {
  value = aws_ecr_repository.repos["tca-api"].repository_url
}

output "mock_server_repository_url" {
  value = aws_ecr_repository.repos["tca-mock-server"].repository_url
}

output "airflow_repository_url" {
  value = aws_ecr_repository.repos["tca-airflow"].repository_url
}

output "angular_repository_url" {
  value = aws_ecr_repository.repos["tca-angular"].repository_url
}
