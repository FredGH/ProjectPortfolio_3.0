variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "project" {
  type    = string
  default = "tca"
}

variable "aws_account_id" {
  type        = string
  description = "AWS account ID — used to construct the ECR registry URL"
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag (Git SHA) to deploy"
}

variable "db_username" {
  type      = string
  default   = "tca_user"
  sensitive = true
}

variable "db_password" {
  type      = string
  sensitive = true
}
