output "endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}

output "port" {
  value = aws_db_instance.main.port
}

output "db_name" {
  value = aws_db_instance.main.db_name
}

output "address" {
  value     = aws_db_instance.main.address
  sensitive = true
}
