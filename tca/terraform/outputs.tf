output "alb_dns_name" {
  description = "Public DNS name of the Application Load Balancer"
  value       = module.alb.dns_name
}

output "cloudfront_url" {
  description = "CloudFront distribution URL for the Angular SPA"
  value       = module.cdn.cloudfront_url
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint (host:port)"
  value       = module.rds.endpoint
  sensitive   = true
}

output "ecr_registry" {
  description = "ECR registry prefix for all images"
  value       = local.ecr_registry
}

output "ecr_repositories" {
  description = "ECR repository URLs by image name"
  value       = module.ecr.repository_urls
}
