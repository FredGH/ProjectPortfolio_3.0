# LocalStack Community environment — tests IAM, Secrets Manager, and S3.
# CloudFront and ECR are LocalStack Pro features; tested with terraform plan only (environments/prod).
# Run with:
#   docker compose -f ../../docker-compose.localstack.yml up -d localstack
#   /opt/homebrew/bin/terraform init && /opt/homebrew/bin/terraform apply -auto-approve
# Requires arm64 terraform (/opt/homebrew/bin/terraform) on Apple Silicon.

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
  region                      = "eu-west-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    ecr            = "http://localhost:4566"
    s3             = "http://localhost:4566"
    cloudfront     = "http://localhost:4566"
    iam            = "http://localhost:4566"
    secretsmanager = "http://localhost:4566"
  }
}

locals {
  name_prefix = "tca-local"
  tags = {
    Project     = "tca"
    Environment = "local"
    ManagedBy   = "terraform"
  }
}

module "iam" {
  source      = "../../modules/iam"
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "secrets" {
  source      = "../../modules/secrets"
  name_prefix = local.name_prefix
  db_username = "tca_user"
  db_password = "tca_password"
  tags        = local.tags
}

# S3 only — CloudFront (aws_cloudfront_*) is LocalStack Pro. ECR is also Pro.
# The full cdn module is tested via `terraform plan` in environments/prod.
resource "aws_s3_bucket" "angular_spa" {
  bucket = "${local.name_prefix}-angular-spa"
  tags   = merge(local.tags, { Name = "${local.name_prefix}-angular-spa" })
}

resource "aws_s3_bucket_public_access_block" "angular_spa" {
  bucket                  = aws_s3_bucket.angular_spa.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "iam_execution_role_arn" {
  value = module.iam.execution_role_arn
}

output "iam_task_role_arn" {
  value = module.iam.task_role_arn
}

output "secret_arns" {
  value = {
    db_credentials = module.secrets.db_credentials_arn
    redis_url      = module.secrets.redis_url_arn
    jwt_private    = module.secrets.jwt_private_key_arn
    jwt_public     = module.secrets.jwt_public_key_arn
    airflow_key    = module.secrets.airflow_secret_key_arn
  }
}

output "s3_bucket_name" {
  value = aws_s3_bucket.angular_spa.id
}

# ECR and CloudFront require LocalStack Pro.
# Validate them with: cd environments/prod && terraform init && terraform plan
