output "db_credentials_arn" {
  value = aws_secretsmanager_secret.db_credentials.arn
}

output "redis_url_arn" {
  value = aws_secretsmanager_secret.redis_url.arn
}

output "jwt_private_key_arn" {
  value = aws_secretsmanager_secret.jwt_private_key.arn
}

output "jwt_public_key_arn" {
  value = aws_secretsmanager_secret.jwt_public_key.arn
}

output "airflow_secret_key_arn" {
  value = aws_secretsmanager_secret.airflow_secret_key.arn
}
