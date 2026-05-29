# Production environment — full AWS deployment.
# Test with: terraform init && terraform plan -var-file=terraform.tfvars
# Deploy with: terraform apply -var-file=terraform.tfvars  (requires real AWS creds)

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "alarm_email" {
  type    = string
  default = ""
}

data "aws_caller_identity" "current" {}

locals {
  name_prefix  = "tca-prod"
  ecr_registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
  tags = {
    Project     = "tca"
    Environment = "prod"
    ManagedBy   = "terraform"
  }
}

module "vpc" {
  source      = "../../modules/vpc"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "iam" {
  source      = "../../modules/iam"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "secrets" {
  source      = "../../modules/secrets"
  name_prefix = local.name_prefix
  db_password = var.db_password
  tags        = local.tags
}

module "ecr" {
  source      = "../../modules/ecr"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "rds" {
  source      = "../../modules/rds"
  name_prefix = local.name_prefix
  subnet_ids  = module.vpc.private_subnet_ids
  sg_id       = module.vpc.sg_rds_id
  db_username = "tca_user"
  db_password = var.db_password
  tags        = local.tags
}

module "elasticache" {
  source      = "../../modules/elasticache"
  name_prefix = local.name_prefix
  subnet_ids  = module.vpc.private_subnet_ids
  sg_id       = module.vpc.sg_redis_id
  tags        = local.tags
}

module "ecs_cluster" {
  source      = "../../modules/ecs/cluster"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "alb" {
  source      = "../../modules/alb"
  name_prefix = local.name_prefix
  vpc_id      = module.vpc.vpc_id
  subnet_ids  = module.vpc.public_subnet_ids
  sg_alb_id   = module.vpc.sg_alb_id
  tags        = local.tags
}

module "cdn" {
  source       = "../../modules/cdn"
  name_prefix  = local.name_prefix
  alb_dns_name = module.alb.dns_name
  tags         = local.tags
}

module "ecs_api" {
  source                     = "../../modules/ecs/api"
  name_prefix                = local.name_prefix
  aws_region                 = var.aws_region
  cluster_arn                = module.ecs_cluster.cluster_arn
  image_url                  = "${local.ecr_registry}/tca-api:${var.image_tag}"
  execution_role_arn         = module.iam.execution_role_arn
  task_role_arn              = module.iam.task_role_arn
  subnet_ids                 = module.vpc.private_subnet_ids
  sg_id                      = module.vpc.sg_api_id
  target_group_arn           = module.alb.api_target_group_arn
  log_group_name             = module.ecs_cluster.log_group_name
  db_secret_arn              = module.secrets.db_credentials_arn
  redis_url_secret_arn       = module.secrets.redis_url_arn
  jwt_private_key_secret_arn = module.secrets.jwt_private_key_arn
  jwt_public_key_secret_arn  = module.secrets.jwt_public_key_arn
  tags                       = local.tags
}

module "ecs_mock_server" {
  source               = "../../modules/ecs/mock_server"
  name_prefix          = local.name_prefix
  aws_region           = var.aws_region
  cluster_arn          = module.ecs_cluster.cluster_arn
  image_url            = "${local.ecr_registry}/tca-mock-server:${var.image_tag}"
  execution_role_arn   = module.iam.execution_role_arn
  task_role_arn        = module.iam.task_role_arn
  subnet_ids           = module.vpc.private_subnet_ids
  sg_id                = module.vpc.sg_mock_id
  target_group_arn     = module.alb.mock_target_group_arn
  log_group_name       = module.ecs_cluster.log_group_name
  db_secret_arn        = module.secrets.db_credentials_arn
  redis_url_secret_arn = module.secrets.redis_url_arn
  tags                 = local.tags
}

# S3 bucket for Airflow remote task logs (shared by scheduler and webserver)
resource "aws_s3_bucket" "airflow_logs" {
  bucket        = "${local.name_prefix}-airflow-logs"
  force_destroy = true
  tags          = merge(local.tags, { Name = "${local.name_prefix}-airflow-logs" })
}

resource "aws_s3_bucket_lifecycle_configuration" "airflow_logs" {
  bucket = aws_s3_bucket.airflow_logs.id
  rule {
    id     = "expire-old-logs"
    status = "Enabled"
    filter {}
    expiration { days = 30 }
  }
}

resource "aws_s3_bucket_public_access_block" "airflow_logs" {
  bucket                  = aws_s3_bucket.airflow_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

module "ecs_airflow_webserver" {
  source                 = "../../modules/ecs/airflow_webserver"
  name_prefix            = local.name_prefix
  aws_region             = var.aws_region
  cluster_arn            = module.ecs_cluster.cluster_arn
  image_url              = "${local.ecr_registry}/tca-airflow:${var.image_tag}"
  execution_role_arn     = module.iam.execution_role_arn
  task_role_arn          = module.iam.task_role_arn
  subnet_ids             = module.vpc.private_subnet_ids
  sg_id                  = module.vpc.sg_airflow_id
  target_group_arn       = module.alb.airflow_target_group_arn
  log_group_name         = module.ecs_cluster.log_group_name
  db_secret_arn          = module.secrets.db_credentials_arn
  redis_url_secret_arn   = module.secrets.redis_url_arn
  airflow_secret_key_arn = module.secrets.airflow_secret_key_arn
  airflow_db_secret_arn  = module.secrets.airflow_db_arn
  alb_dns_name           = module.alb.dns_name
  tags                   = local.tags
}

module "ecs_airflow_scheduler" {
  source                 = "../../modules/ecs/airflow_scheduler"
  name_prefix            = local.name_prefix
  aws_region             = var.aws_region
  cluster_arn            = module.ecs_cluster.cluster_arn
  image_url              = "${local.ecr_registry}/tca-airflow:${var.image_tag}"
  execution_role_arn     = module.iam.execution_role_arn
  task_role_arn          = module.iam.task_role_arn
  subnet_ids             = module.vpc.private_subnet_ids
  sg_id                  = module.vpc.sg_airflow_id
  log_group_name         = module.ecs_cluster.log_group_name
  db_secret_arn          = module.secrets.db_credentials_arn
  redis_url_secret_arn   = module.secrets.redis_url_arn
  airflow_secret_key_arn = module.secrets.airflow_secret_key_arn
  airflow_db_secret_arn  = module.secrets.airflow_db_arn
  tags                   = local.tags
}

module "observability" {
  source          = "../../modules/observability"
  name_prefix     = local.name_prefix
  aws_region      = var.aws_region
  log_group_name  = module.ecs_cluster.log_group_name
  rds_instance_id = "${local.name_prefix}-postgres"
  alarm_email     = var.alarm_email
  tags            = local.tags
}

output "alb_dns_name" {
  value = module.alb.dns_name
}

output "cloudfront_url" {
  value = module.cdn.cloudfront_url
}

output "rds_endpoint" {
  value     = module.rds.endpoint
  sensitive = true
}

output "rds_address" {
  value     = module.rds.address
  sensitive = true
}

output "redis_connection_string" {
  value     = module.elasticache.connection_string
  sensitive = true
}

output "ecr_repositories" {
  value = module.ecr.repository_urls
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}

output "api_sg_id" {
  value = module.vpc.sg_api_id
}

output "cloudfront_distribution_id" {
  value = module.cdn.cloudfront_distribution_id
}

output "s3_spa_bucket" {
  value = module.cdn.s3_bucket_name
}

output "dashboard_url" {
  value = module.observability.dashboard_url
}
