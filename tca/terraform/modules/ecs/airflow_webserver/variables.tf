variable "name_prefix" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "cluster_arn" {
  type = string
}

variable "image_url" {
  type = string
}

variable "execution_role_arn" {
  type = string
}

variable "task_role_arn" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "sg_id" {
  type = string
}

variable "target_group_arn" {
  type = string
}

variable "log_group_name" {
  type = string
}

variable "db_secret_arn" {
  type = string
}

variable "redis_url_secret_arn" {
  type = string
}

variable "airflow_secret_key_arn" {
  type = string
}

variable "airflow_db_secret_arn" {
  type = string
}

variable "cpu" {
  type    = number
  default = 1024
}

variable "memory" {
  type    = number
  default = 2048
}

variable "alb_dns_name" {
  type = string
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "tags" {
  type    = map(string)
  default = {}
}
