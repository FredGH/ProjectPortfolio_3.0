output "endpoint" {
  value = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "port" {
  value = aws_elasticache_cluster.redis.port
}

output "connection_string" {
  value     = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:${aws_elasticache_cluster.redis.port}/0"
  sensitive = true
}
